"""Real model + MCP promise workflow with an explicitly adjusted test clock.

This is a short synthetic check, never a 120-minute completion claim. Only this
new fixture's clock is changed; user preview sessions remain untouched.
"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
import httpx
from sqlalchemy import select
from dayzero.backend.db import DB, Session, Job, Event, Record, Task
from dayzero.backend.domain import tick_session, close_session, digest
from dayzero.shared.config import settings
from run_full_verification import preflight


async def main():
    preflight()
    worker=None;sid=None
    result={'synthetic_fixture':True,'actual_model':True,'adjusted_clock':True,'full_120_minutes':False,'completed':False}
    try:
        worker=await asyncio.create_subprocess_exec(sys.executable,'-m','dayzero.workers.ai',
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        async with httpx.AsyncClient(base_url=settings().backend_url,timeout=20) as client:
            reg=await client.post('/auth/register',json={'email':'promise-'+uuid.uuid4().hex+'@example.com',
                'password':uuid.uuid4().hex,'name':'합성 약속 검증 기업'});reg.raise_for_status()
            inv=await client.post('/admin/invitations',headers={'Authorization':'Bearer '+reg.json()['token']},
                json={'alias':'합성 약속 검증','email':'synthetic@example.com'});inv.raise_for_status()
            sid=inv.json()['id'];auth={'Authorization':'Bearer '+urlparse(inv.json()['url']).path.rsplit('/',1)[1]}
            (await client.post('/sessions/'+sid+'/consent',headers=auth,json={'accepted':True})).raise_for_status()
            start=await client.post('/sessions/'+sid+'/start',headers=auth);start.raise_for_status()
            result.update(session_id=sid,binding=start.json()['binding'])
            async def wait_for(condition):
                until=time.monotonic()+460
                while time.monotonic()<until:
                    response=await client.get('/sessions/'+sid,headers=auth);response.raise_for_status();w=response.json()
                    if any(j['status']=='failed' for j in w['jobs']):
                        raise RuntimeError('model_job_failed')
                    if condition(w):return w
                    await asyncio.sleep(.3)
                raise TimeoutError('synthetic promise deadline')
            await wait_for(lambda w:all(any(j['agent_id']==a and j['status']=='done' for j in w['jobs']) for a in ['leader','colleague']))
            def advance(seconds):
                with DB.begin() as db:
                    session=db.get(Session,sid);shift=session.started-(time.time()-seconds)
                    session.started-=shift
                    for event in db.scalars(select(Event).where(Event.session_id==sid)):event.due-=shift
                    tick_session(db,session);tick_session(db,session)
            advance(1810)
            sent=await client.post('/sessions/'+sid+'/messages',headers=auth,json={
                'flow':'collaboration','channel':'team','recipients':['colleague'],'operation_id':uuid.uuid4().hex,
                'text':'지금은 문제 정의를 정리 중이라 10분 뒤 사례 비교를 함께 보겠습니다. '
                '동료가 맡은 4번 파트의 비교 기준 보완을 동료 담당 태스크 하나로 만들고 10분 뒤 약속 시점을 남겨 주세요. '
                '그 전에는 기획안 변경을 보류해 주세요. 약속 시점에는 무료 알림 비교 기준을 4번 파트에 저장하고 완료를 알려 주세요.'})
            sent.raise_for_status();mid=sent.json()['id']
            w=await wait_for(lambda w:any(r['data'].get('reply_to')==mid for r in w['records']))
            promised=[t for t in w['tasks'] if t['status']=='waiting']
            assert len(promised)==1,'promise_not_persisted'
            task=promised[0];assert 2380<=task['due_elapsed']<=2500,'promise_time_incorrect'
            part=next(p for p in w['parts'] if p['number']==4)
            assert part['body']=='','work_executed_before_promise'
            result.update(promise_task_id=task['id'],due_elapsed=task['due_elapsed'],document_unchanged_before_due=True)
            advance(task['due_elapsed']+2)
            w=await wait_for(lambda w:any(t['id']==task['id'] and t['status']=='done' for t in w['tasks']) and
                any(r['actor']=='colleague' and r['data'].get('mutation_ids') and r['data'].get('reply_to') in
                    [t['id'] for t in w['records'] if t['kind']=='work_trigger' and t['data'].get('trigger','').startswith('promise:')] for r in w['records']))
            with DB.begin() as db:
                session=db.get(Session,sid);tick_session(db,session);tick_session(db,session)
                triggers=[r for r in db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='work_trigger')) if r.data.get('trigger','').startswith('promise:')]
                edits=list(db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='part',Record.actor=='colleague')))
                jobs=list(db.scalars(select(Job).where(Job.session_id==sid)))
                assert len(triggers)==1 and len(edits)==1
                assert db.get(Task,task['id']).status=='done'
                result.update(completed=True,promise_triggers=1,document_changes=1,
                    jobs=[{'agent':j.agent_id,'model_calls':j.model_calls,'tool_calls':len(j.tool_operations),'status':j.status} for j in jobs])
    except Exception as error:
        result['error']=type(error).__name__
        if sid:
            with DB.begin() as db:
                result['job_errors']=[j.error for j in db.scalars(select(Job).where(Job.session_id==sid)) if j.error]
    finally:
        if worker and worker.returncode is None:
            worker.terminate()
            try:await asyncio.wait_for(worker.wait(),10)
            except TimeoutError:worker.kill();await worker.wait()
        if sid:
            with DB.begin() as db:
                session=db.get(Session,sid)
                assert session.alias=='합성 약속 검증'
                close_session(db,session)
                for j in db.scalars(select(Job).where(Job.session_id==sid,Job.status.in_(['pending','running']))):
                    j.status,j.error='cancelled','synthetic_promise_cleanup'
                session.token_hash=digest(uuid.uuid4().hex)
        Path('docs/evidence/revision-promised-work.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not result['completed']:raise SystemExit(1)


if __name__=='__main__':asyncio.run(main())
