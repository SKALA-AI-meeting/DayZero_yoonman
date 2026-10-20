"""Crash an isolated Backend after commit/before publish, then restore its jobs.

Uses a new disposable database and RabbitMQ vhost. It never stops the user's
Backend or consumes its queues, and makes no model calls.
"""
import asyncio
import json
import os
import socket
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote, unquote

import aio_pika
import httpx
import psycopg
from psycopg import sql
from dayzero.shared.config import settings


def reserve_port():
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    return sock,sock.getsockname()[1]


async def main():
    cfg=settings()
    run_id=uuid.uuid4().hex
    name='dayzero_outbox_'+run_id
    base=cfg.database_url.replace('postgresql+psycopg://','postgresql://')
    parsed=urlsplit(base)
    isolated=urlunsplit(parsed._replace(path='/'+name))
    rabbit=urlsplit(cfg.rabbitmq_url)
    isolated_rabbit=urlunsplit(rabbit._replace(path='/'+name))
    management='http://'+rabbit.hostname+':15673'
    auth=(unquote(rabbit.username),unquote(rabbit.password))
    db_admin=psycopg.connect(base,autocommit=True)
    child=None;created_db=False;created_vhost=False
    blocked,blocked_port=reserve_port()
    reserved,port=reserve_port();reserved.close()
    env={**os.environ,'DATABASE_URL':isolated.replace('postgresql://','postgresql+psycopg://'),
        'RABBITMQ_URL':f'amqp://unused:unused@127.0.0.1:{blocked_port}/',
        'REPORT_LLM_BACKEND':'openai','REPORT_MODEL':'gpt-4o-mini','OPENAI_API_KEY':''}
    result={'synthetic_fixture':True,'isolated_database_and_vhost':True,
        'actual_backend':True,'actual_rabbitmq':True,'actual_model':False,'completed':False}
    async def launch(client):
        nonlocal child
        child=await asyncio.create_subprocess_exec(sys.executable,'-m','uvicorn','dayzero.backend.main:app',
            '--host','127.0.0.1','--port',str(port),'--no-access-log',env=env,
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        for _ in range(100):
            if child.returncode is not None:raise RuntimeError('isolated_backend_exited')
            try:
                response=await client.get('/health')
                if response.status_code==200:return
            except httpx.TransportError:pass
            await asyncio.sleep(.1)
        raise TimeoutError('isolated_backend_start')
    try:
        db_admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)));created_db=True
        migration=await asyncio.create_subprocess_exec('services/dayzero/.venv/bin/alembic','-c',
            'services/dayzero/alembic.ini','upgrade','head',env=env,
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        assert await migration.wait()==0,'isolated_migration_failed'
        async with httpx.AsyncClient(base_url=management,auth=auth,timeout=5) as manager:
            response=await manager.put('/api/vhosts/'+name,json={});response.raise_for_status();created_vhost=True
            response=await manager.put('/api/permissions/'+name+'/'+quote(auth[0],safe=''),
                json={'configure':'.*','write':'.*','read':'.*'});response.raise_for_status()
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=5) as client:
            await launch(client)
            response=await client.post('/auth/register',json={'email':run_id+'@example.com',
                'password':uuid.uuid4().hex,'name':'합성 Backend 복구 검증'});response.raise_for_status()
            admin={'Authorization':'Bearer '+response.json()['token']}
            response=await client.post('/admin/invitations',headers=admin,json={'alias':'합성 복구','email':'synthetic@example.com'})
            response.raise_for_status();invite=response.json();sid=invite['id']
            candidate={'Authorization':'Bearer '+urlsplit(invite['url']).path.rsplit('/',1)[1]}
            response=await client.post('/sessions/'+sid+'/consent',headers=candidate,json={'accepted':True});response.raise_for_status()
            response=await client.post('/sessions/'+sid+'/start',headers=candidate);response.raise_for_status()
            with psycopg.connect(isolated) as db:
                jobs=db.execute('SELECT id,agent_id,published,status FROM jobs WHERE session_id=%s ORDER BY agent_id',(sid,)).fetchall()
                assert len(jobs)==2 and all(not j[2] and j[3]=='pending' for j in jobs),'commit_before_publish_not_observed'
            result.update(session_id=sid,committed_unpublished_jobs=len(jobs))
            child.kill();await child.wait();child=None
            result['crash']='SIGKILL after committed jobs, before broker publication'
            env['RABBITMQ_URL']=isolated_rabbit
            await launch(client)
            for _ in range(100):
                with psycopg.connect(isolated) as db:
                    recovered=db.execute('SELECT id,agent_id,published,status FROM jobs WHERE session_id=%s ORDER BY agent_id',(sid,)).fetchall()
                if len(recovered)==2 and all(j[2] for j in recovered):break
                await asyncio.sleep(.1)
            assert [j[:2] for j in recovered]==[j[:2] for j in jobs] and all(j[2] for j in recovered),'outbox_not_recovered'
            response=await client.post('/sessions/'+sid+'/start',headers=candidate);response.raise_for_status()
            async with await aio_pika.connect_robust(isolated_rabbit) as connection:
                channel=await connection.channel()
                delivered=[]
                for jid,agent,*_ in jobs:
                    queue=await channel.declare_queue('dayzero.ai.'+agent,passive=True)
                    message=await queue.get(timeout=5)
                    assert json.loads(message.body)=={'job_id':jid} and message.message_id==jid
                    assert message.delivery_mode==aio_pika.DeliveryMode.PERSISTENT
                    delivered.append(jid);await message.ack()
                await asyncio.sleep(1)
                for _,agent,*_ in jobs:
                    queue=await channel.declare_queue('dayzero.ai.'+agent,passive=True)
                    assert await queue.get(fail=False) is None,'duplicate_publication'
            with psycopg.connect(isolated) as db:
                assert db.execute('SELECT count(*) FROM jobs WHERE session_id=%s',(sid,)).fetchone()[0]==2
                assert db.execute('SELECT count(*) FROM records WHERE session_id=%s AND operation_id LIKE %s',(sid,'kickoff:%')).fetchone()[0]==2
            result.update(completed=True,same_job_ids_recovered=True,persistent_deliveries=len(delivered),
                duplicate_publications=0,duplicate_kickoffs=0)
    except Exception as error:
        result['error']=str(error) if isinstance(error,AssertionError) else type(error).__name__
    finally:
        if child and child.returncode is None:
            child.terminate()
            try:await asyncio.wait_for(child.wait(),5)
            except TimeoutError:child.kill();await child.wait()
        blocked.close()
        if created_vhost:
            async with httpx.AsyncClient(base_url=management,auth=auth,timeout=5) as manager:
                response=await manager.delete('/api/vhosts/'+name);response.raise_for_status()
        if created_db:db_admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        db_admin.close()
        result['disposable_resources_removed']=True
        Path('docs/evidence/revision-backend-outbox.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not result['completed']:raise SystemExit(1)


if __name__=='__main__':asyncio.run(main())
