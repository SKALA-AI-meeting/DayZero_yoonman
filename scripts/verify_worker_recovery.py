"""Actual API/Graph/MCP/queue recovery on a new fictional fixture.

Run with the normal AI Worker stopped. This script kills only its own Worker.
The key is inherited in memory; no credentials or raw exception text are saved.
"""
import asyncio
import json
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import aio_pika
import httpx
from sqlalchemy import select
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from dayzero.agents.colleague.graph import compile as compile_graph
from dayzero.backend.db import DB, Job, Record, Session, Company, Task
from dayzero.shared.config import settings


async def start_worker():
    return await asyncio.create_subprocess_exec(sys.executable, '-m', 'dayzero.workers.ai',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)


async def stop_worker(worker):
    if worker and worker.returncode is None:
        worker.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(worker.wait(), 8)
        except TimeoutError:
            worker.kill()
            await worker.wait()


async def wait_for(check, timeout=120):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        await asyncio.sleep(.05)
    raise TimeoutError('recovery verification deadline')


async def main():
    existing = subprocess.run(['pgrep', '-f',
        r'^.*python -m dayzero\.workers\.ai$'], capture_output=True)
    if existing.returncode == 0:
        raise RuntimeError('stop the normal AI Worker before this isolated check')
    from sqlalchemy import func
    with DB.begin() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(['pending','running'])))==0, 'existing pending jobs must be stopped first'
    worker = None
    sid = None
    result = {'actual_model': True, 'synthetic_fixture': True, 'completed': False}
    try:
        worker = await start_worker()
        async with httpx.AsyncClient(base_url=settings().backend_url, timeout=20) as client:
            reg = await client.post('/auth/register', json={'email':'recovery-'+uuid.uuid4().hex+'@example.com',
                'password':uuid.uuid4().hex, 'name':'합성 Worker 복구 검증 기업'})
            reg.raise_for_status()
            admin = {'Authorization':'Bearer '+reg.json()['token']}
            inv = await client.post('/admin/invitations', headers=admin,
                json={'alias':'합성 Worker 복구 검증','email':'recovery@example.com'})
            inv.raise_for_status(); sid = inv.json()['id']
            auth = {'Authorization':'Bearer '+urlparse(inv.json()['url']).path.rsplit('/',1)[1]}
            (await client.post('/sessions/'+sid+'/consent', headers=auth,
                json={'accepted':True})).raise_for_status()
            started = await client.post('/sessions/'+sid+'/start', headers=auth)
            started.raise_for_status(); result.update(session_id=sid, binding=started.json()['binding'])
            def kickoff_done():
                with DB.begin() as db:
                    job=db.scalar(select(Job).where(Job.session_id==sid,Job.agent_id=='colleague'))
                    return job.status=='done'
            await wait_for(kickoff_done)
            workspace=(await client.get('/sessions/'+sid,headers=auth)).json()
            part=workspace['parts'][3]
            created=await client.post('/sessions/'+sid+'/tasks',headers=auth,json={
                'title':'복구 검증 문서와 태스크 원자 저장','owner':'colleague','part_id':part['id'],
                'note':'complete_task 도구로 문서와 완료 상태를 함께 저장해 주세요. 본문은 정확히 “무료 모임 일정 알림 사례 비교: 알림 동의와 동네 단위 공개를 비교합니다.”로 하고 저장 후 짧게 알려 주세요.',
                'operation_id':uuid.uuid4().hex})
            created.raise_for_status();tid=created.json()['id']
            with DB.begin() as db:
                trigger=next(r for r in db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='work_trigger')) if r.data.get('target',{}).get('id')==tid)
                jid=db.scalar(select(Job.id).where(Job.input_id==trigger.id))
            def mutation_saved():
                with DB.begin() as db:
                    records=list(db.scalars(select(Record).where(Record.session_id==sid,
                        Record.kind=='part',Record.actor=='colleague')))
                    if not records:
                        return None
                    job=db.get(Job,jid)
                    if job.status!='running':
                        raise RuntimeError('job completed before the crash could be injected')
                    return records[0].data['after']['version']
            version=await wait_for(mutation_saved)
            worker.kill(); await worker.wait()
            killed_at=time.monotonic()
            print('own Worker killed after actual MCP mutation and before job completion',flush=True)
            config={'configurable':{'thread_id':sid+':colleague'}}
            async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
                state=await compile_graph(saver).aget_state(config)
                assert state.next and state.values['job_id']==jid
                result['pending_checkpoint_nodes']=list(state.next)
            worker=await start_worker()
            def completed():
                with DB.begin() as db:
                    job=db.get(Job,jid)
                    if job.status=='failed':
                        raise RuntimeError('resumed job failed: '+str(job.error))
                    return job.status=='done'
            await wait_for(completed,160)
            result['recovery_seconds']=round(time.monotonic()-killed_at,3)
            async with await aio_pika.connect_robust(settings().rabbitmq_url) as connection:
                channel=await connection.channel(publisher_confirms=True)
                for _ in range(2):
                    await channel.default_exchange.publish(aio_pika.Message(
                        body=json.dumps({'job_id':jid}).encode(),delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                        routing_key='dayzero.ai.colleague')
            await asyncio.sleep(2)
            with DB.begin() as db:
                job=db.get(Job,jid)
                changes=list(db.scalars(select(Record).where(Record.session_id==sid,
                    Record.kind=='part',Record.actor=='colleague')))
                answers=list(db.scalars(select(Record).where(Record.session_id==sid,Record.operation_id=='job:'+jid)))
                assert job.attempts==2 and job.status=='done'
                assert len(changes)==1 and changes[0].data['after']['version']==version
                assert len(answers)==1
                task_changes=list(db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='task',Record.actor=='colleague')))
                assert len(task_changes)==1 and db.get(Task,tid).status=='done'
                result.update(queue_attempts=job.attempts,model_calls=job.model_calls,
                    tool_operations=len(job.tool_operations),part_changes=len(changes),answers=len(answers),
                    task_completions=len(task_changes),duplicate_deliveries=2,completed=True)
            print(json.dumps(result,ensure_ascii=False),flush=True)
            (await client.post('/sessions/'+sid+'/submit',headers=auth)).raise_for_status()
    finally:
        await stop_worker(worker)
        if sid:
            with DB.begin() as db:
                fixture=db.get(Session,sid)
                company=db.get(Company,fixture.company_id)
                assert company.name=='합성 Worker 복구 검증 기업' and company.email.startswith('recovery-')
                for job in db.scalars(select(Job).where(Job.session_id==sid,Job.status.in_(['pending','running']))):
                    job.status,job.error='cancelled','synthetic_recovery_cleanup'
                if fixture.state=='active':fixture.state,fixture.ended='completed',time.time()
        Path('docs/evidence/revision-worker-recovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print('recovery verification failed:',type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
