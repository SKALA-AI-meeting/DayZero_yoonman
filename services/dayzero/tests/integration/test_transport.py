"""Actual RabbitMQ, MCP HTTP and Postgres checkpoint tests; no real LLM implied."""
import asyncio
import json
import uuid
from urllib.parse import urlparse
import aio_pika
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph, START, END
from typing import TypedDict
from sqlalchemy import select
from dayzero.backend.db import DB, Job
from dayzero.shared.config import settings


async def prepare(flow='chat'):
    async with httpx.AsyncClient(base_url=settings().backend_url,timeout=20) as c:
        reg=await c.post('/auth/register',json={'email':str(uuid.uuid4())+'@example.com','password':'test-password-123'})
        reg.raise_for_status();admin={'Authorization':'Bearer '+reg.json()['token']}
        inv=await c.post('/admin/invitations',headers=admin,json={'alias':'MCP 테스트','email':'test@example.com'})
        inv.raise_for_status();sid=inv.json()['id'];token=urlparse(inv.json()['url']).path.rsplit('/',1)[1]
        auth={'Authorization':'Bearer '+token}
        (await c.post('/sessions/'+sid+'/consent',headers=auth,json={'accepted':True})).raise_for_status()
        (await c.post('/sessions/'+sid+'/start',headers=auth)).raise_for_status()
        if flow in ['leader','colleague']:
            (await c.post('/sessions/'+sid+'/messages',headers=auth,json={
                'flow':'collaboration','channel':'team','recipients':[flow],'text':'합성 Graph 검증 메시지','operation_id':str(uuid.uuid4())})).raise_for_status()
            with DB.begin() as db:
                first=db.scalar(select(Job).where(Job.session_id==sid,Job.agent_id==flow).order_by(Job.sequence).limit(1))
                first.status='cancelled'
        if flow=='chat':
            (await c.post('/sessions/'+sid+'/messages',headers=auth,json={
                'flow':'chat','channel':'assistant','text':'운영 정책 문서를 확인해 주세요.','operation_id':str(uuid.uuid4())})).raise_for_status()
        with DB.begin() as db:
            jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.agent_id==flow,Job.status=='pending').order_by(Job.sequence.desc()))
        claimed=await c.post('/internal/jobs/'+jid+'/claim',headers={
            'X-Internal-Secret':settings().internal_secret.get_secret_value()})
        claimed.raise_for_status()
        return {**claimed.json(), 'candidate_auth': auth}


async def test_actual_business_and_rag_mcp_calls():
    job=await prepare()
    body={'job_id':job['id'],'job_token':job['token']}
    async with streamable_http_client(settings().business_mcp_url) as (read,write,_):
        async with ClientSession(read,write) as client:
            await client.initialize()
            listed=await client.list_tools();assert 'business_call' in [t.name for t in listed.tools]
            result=await client.call_tool('business_call',{**body,'name':'get_workspace','arguments':{},'operation_id':'read'})
            assert not result.isError
            payload=result.structuredContent or json.loads(result.content[0].text)
            assert len(payload['parts'])==5
            denied=await client.call_tool('business_call',{**body,'name':'update_part','arguments':{},'operation_id':'denied'})
            assert denied.isError
    async with httpx.AsyncClient(base_url=settings().backend_url) as c:
        docs=(await c.get('/internal/jobs/'+job['id']+'/documents',headers={'Authorization':'Bearer '+job['token']})).json()
    assert len(docs)==4
    async with streamable_http_client(settings().rag_mcp_url) as (read,write,_):
        async with ClientSession(read,write) as client:
            await client.initialize()
            assert 'rag_call' in [t.name for t in (await client.list_tools()).tools]
            result=await client.call_tool('rag_call',{**body,'name':'get_document','arguments':{'document_id':docs[0]['id']}})
            assert not result.isError
            hidden=docs[0]['id'].split(':')[0]+':interviews:v1'
            result=await client.call_tool('rag_call',{**body,'name':'get_document','arguments':{'document_id':hidden}})
            assert result.isError


async def test_actual_queue_confirm_delivery_and_ack():
    async with await aio_pika.connect_robust(settings().rabbitmq_url) as connection:
        channel=await connection.channel(publisher_confirms=True)
        queue=await channel.declare_queue('test.'+str(uuid.uuid4()),exclusive=True)
        payload=json.dumps({'job_id':str(uuid.uuid4())}).encode()
        confirmed=await channel.default_exchange.publish(aio_pika.Message(body=payload),routing_key=queue.name)
        assert confirmed is not None
        message=await queue.get(timeout=5)
        assert message.body==payload
        await message.ack()
        assert await queue.get(fail=False) is None


async def test_actual_mcp_version_conflict_returns_repair_feedback():
    from dayzero.mcp_client.client import execute
    from dayzero.shared.internal import Internal
    from dayzero.llm.schemas import Proposal
    job=await prepare('colleague')
    ctx=await Internal(job['id'],job['token']).call('context')
    part=next(p for p in ctx['parts'] if p['number']==4)
    def change(version):
        return Proposal(name='update_part',arguments={'part_id':part['id'],
            'change':{'version':version,'body':'합성 MCP 버전 충돌 후 저장'}})
    feedback=await execute('colleague',job['id'],job['token'],change(part['version']+1),
        'conflict',ctx['event_version'])
    assert feedback['error']=='version_conflict'
    saved=await execute('colleague',job['id'],job['token'],change(part['version']),
        'repaired',ctx['event_version'])
    assert saved['part']['version']==part['version']+1


class Counter(TypedDict):
    value:int


def counter(saver):
    graph=StateGraph(Counter)
    graph.add_node('increment',lambda s:{'value':s['value']+1})
    graph.add_edge(START,'increment');graph.add_edge('increment',END)
    return graph.compile(checkpointer=saver)


async def test_postgres_checkpoint_reopen_and_thread_isolation():
    sid=str(uuid.uuid4());config={'configurable':{'thread_id':sid+':colleague'}}
    async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
        await saver.setup();g=counter(saver)
        assert (await g.ainvoke({'value':4},config))['value']==5
    async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
        g=counter(saver)
        assert (await g.aget_state(config)).values=={'value':5}
        assert not (await g.aget_state({'configurable':{'thread_id':sid+':chat'}})).values


async def test_actual_graph_mcp_change_resume_without_duplicate(monkeypatch):
    """Mock only the model; actual Graph/MCP/backend/DB/checkpoint recovery."""
    from dayzero.agents.colleague.graph import compile as compile_graph
    from dayzero.llm.schemas import Reply,ModelError
    from dayzero.shared.internal import Internal
    from dayzero.backend.db import Record
    job=await prepare('colleague')
    internal=Internal(job['id'],job['token'])
    ctx=await internal.call('context');part=next(p for p in ctx['parts'] if p['number']==4)
    step=0
    async def synthetic_model(request, config, reserve=None):
        nonlocal step
        step+=1
        if step==1:
            return Reply(kind='tools',text='',tools=[{'name':'update_part','arguments':{
                'part_id':part['id'],'change':{'version':part['version'],'body':'합성 동료 작성 내용'}}}]),None
        if step==2:raise ModelError('injected_process_stop')
        return Reply(kind='final',text='담당 파트를 공유했습니다.',tools=[]),None
    monkeypatch.setattr('dayzero.agents.graph.complete',synthetic_model)
    config={'configurable':{'thread_id':job['session_id']+':colleague'}}
    async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
        await saver.setup();graph=compile_graph(saver)
        with pytest.raises(ModelError,match='injected_process_stop'):
            await graph.ainvoke({'job_id':job['id'],'agent_id':'colleague'},config,context={'job_token':job['token']})
        state=await graph.aget_state(config)
        assert state.next==('respond',)
        assert 'token' not in state.values
    # A new saver and compiled Graph read the pending task and resume it.
    async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
        graph=compile_graph(saver)
        await graph.ainvoke(None,config,context={'job_token':job['token']})
    with DB.begin() as db:
        changes=list(db.scalars(select(Record).where(Record.session_id==job['session_id'],Record.kind=='part')))
        assert len(changes)==1 and changes[0].actor=='colleague'
        assert changes[0].data['after']['version']==part['version']+1
        assert db.get(Job,job['id']).status=='done'
    import psycopg
    with psycopg.connect(settings().checkpoint_database_url) as db:
        for (blob,) in db.execute('SELECT blob FROM checkpoint_blobs WHERE thread_id=%s',
                                 (job['session_id']+':colleague',)):
            if blob: assert job['token'].encode() not in bytes(blob)
        for (metadata,) in db.execute('SELECT metadata FROM checkpoints WHERE thread_id=%s',
                                     (job['session_id']+':colleague',)):
            assert job['token'] not in json.dumps(metadata)


async def test_realtime_event_delivery_while_graph_waits(monkeypatch):
    import websockets
    from dayzero.agents.colleague.graph import compile as compile_graph
    from dayzero.llm.schemas import Reply
    from dayzero.backend.db import Session, Event
    from langgraph.checkpoint.memory import InMemorySaver
    import time
    job=await prepare('colleague');entered=asyncio.Event();release=asyncio.Event()
    async def slow_model(request,config,reserve=None):
        entered.set();await release.wait()
        return Reply(kind='final',text='합성 지연 응답입니다.',tools=[]),None
    monkeypatch.setattr('dayzero.agents.graph.complete',slow_model)
    graph=compile_graph(InMemorySaver())
    task=asyncio.create_task(graph.ainvoke({'job_id':job['id'],'agent_id':'colleague'},
        {'configurable':{'thread_id':job['session_id']+':colleague'}},context={'job_token':job['token']}))
    await asyncio.wait_for(entered.wait(),5)
    uri=settings().backend_url.replace('http://','ws://')+'/sessions/'+job['session_id']+'/ws'
    async with websockets.connect(uri) as ws:
        await ws.send(job['candidate_auth']['Authorization'].removeprefix('Bearer '))
        first=json.loads(await ws.recv());assert first['state']=='active'
        await ws.send(json.dumps({'received':[r['id'] for r in first['records']]}))
        with DB.begin() as db:
            s=db.get(Session,job['session_id']);s.started=time.time()-1801
        async with asyncio.timeout(5):
            while True:
                snapshot=json.loads(await ws.recv())
                events=[r for r in snapshot['records'] if r['data'].get('event_key')=='help']
                await ws.send(json.dumps({'received':[r['id'] for r in snapshot['records']]}))
                if events:break
        assert not task.done()
        await asyncio.sleep(.6)
    with DB.begin() as db:
        event=db.scalar(select(Event).where(Event.session_id==job['session_id'],Event.key=='help'))
        assert event.stored_at is not None and event.received_at is not None
    release.set();await asyncio.wait_for(task,5)


async def test_backend_remains_responsive_while_claim_waits_for_db_lock():
    job=await prepare('chat')
    async with httpx.AsyncClient(base_url=settings().backend_url,timeout=4) as client:
        with DB.begin() as db:
            db.scalar(select(Job).where(Job.id==job['id']).with_for_update())
            blocked=asyncio.create_task(client.post('/internal/jobs/'+job['id']+'/claim',headers={
                'X-Internal-Secret':settings().internal_secret.get_secret_value()}))
            await asyncio.sleep(.1)
            for _ in range(4):
                health=await client.get('/health',timeout=1)
                assert health.status_code==200
                await asyncio.sleep(.1)
        result=await asyncio.wait_for(blocked,4)
        assert result.status_code==409


@pytest.mark.parametrize('agent',['leader','colleague','chat'])
async def test_each_agent_checkpoint_can_resume_without_another_person(monkeypatch,agent):
    from dayzero.agents.graph import build_graph
    from dayzero.llm.schemas import Reply, ModelError
    from dayzero.shared.internal import Internal
    job=await prepare(agent);step=0
    docs=await Internal(job['id'],job['token']).call('documents')
    doc=next(d for d in docs if d['title']=='운영 정책')
    async def model(*args,**kwargs):
        nonlocal step
        step+=1
        if step==1:return Reply(kind='tools',text='',tools=[{'name':'get_document','arguments':{'document_id':doc['id']}}]),None
        if step==2:raise ModelError('synthetic_stop')
        return Reply(kind='final',text='현재 업무 상태를 확인했습니다.',tools=[]),None
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    config={'configurable':{'thread_id':job['session_id']+':'+agent}}
    async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
        graph=build_graph(agent,saver)
        with pytest.raises(ModelError,match='synthetic_stop'):
            await graph.ainvoke({'job_id':job['id'],'agent_id':agent},config,context={'job_token':job['token']})
        assert (await graph.aget_state(config)).next==('respond',)
    async with AsyncPostgresSaver.from_conn_string(settings().checkpoint_database_url) as saver:
        graph=build_graph(agent,saver)
        await graph.ainvoke(None,config,context={'job_token':job['token']})
        for other in set(['leader','colleague','chat'])-{agent}:
            assert not (await graph.aget_state({'configurable':{'thread_id':job['session_id']+':'+other}})).values
    with DB.begin() as db:
        assert db.get(Job,job['id']).status=='done'
