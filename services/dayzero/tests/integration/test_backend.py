"""Real PostgreSQL tests. Any injected model result is explicitly synthetic."""
import time
import uuid
from urllib.parse import urlparse
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from dayzero.backend.main import app
from dayzero.backend.db import DB, Session, Record, Job, Event
from dayzero.backend.domain import tick_session,elapsed
from dayzero.shared.config import settings
from dayzero.reporting.build import baseline, assemble_report, evidence_catalog, utilization_links

client=TestClient(app)


def company():
    r=client.post('/auth/register',json={'email':str(uuid.uuid4())+'@example.com','password':'test-password-123','name':'테스트 기업'})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}


def make_session():
    admin=company()
    r=client.post('/admin/invitations',headers=admin,json={'alias':'지원자','email':'candidate@example.com'})
    assert r.status_code==200,r.text
    sid=r.json()['id']; token=urlparse(r.json()['url']).path.rsplit('/',1)[1]
    auth={'Authorization':'Bearer '+token}
    assert client.post('/sessions/'+sid+'/start',headers=auth).status_code==409
    assert client.post('/sessions/'+sid+'/consent',headers=auth,json={'accepted':True}).status_code==200
    assert client.post('/sessions/'+sid+'/start',headers=auth).status_code==200
    return sid,auth,admin


def claim(jid):
    r=client.post('/internal/jobs/'+jid+'/claim',headers={'X-Internal-Secret':settings().internal_secret.get_secret_value()})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}


def test_invite_consent_start_is_idempotent_and_scoped():
    sid,auth,admin=make_session()
    other=company()
    assert client.get('/admin/sessions/'+sid+'/report',headers=other).status_code==404
    assert client.get('/sessions/'+sid,headers=admin).status_code==404
    assert client.get('/sessions/'+sid,headers={'Authorization':'Bearer invalid'}).status_code==404
    assert client.post('/sessions/'+sid+'/start',headers=auth).status_code==200
    w=client.get('/sessions/'+sid,headers=auth).json()
    assert len(w['parts'])==5
    assert len([r for r in w['records'] if r['actor']=='leader'])==1
    assert len(client.get('/sessions/'+sid+'/documents',headers=auth).json())==4


def test_document_release_and_foreign_document_denied():
    sid,auth,_=make_session();other,other_auth,_=make_session()
    foreign=client.get('/sessions/'+other+'/documents',headers=other_auth).json()[0]['id']
    assert client.get('/sessions/'+sid+'/documents/'+foreign,headers=auth).status_code==404
    with DB.begin() as db:
        s=db.get(Session,sid); hidden=s.company_id+':interviews:v1'
    assert client.get('/sessions/'+sid+'/documents/'+hidden,headers=auth).status_code==404
    with DB.begin() as db:
        s=db.get(Session,sid);s.started=time.time()-3901
    assert client.get('/sessions/'+sid+'/documents/'+hidden,headers=auth).status_code==200


def test_versions_author_and_duplicate_operations():
    sid,auth,_=make_session(); w=client.get('/sessions/'+sid,headers=auth).json();p=w['parts'][0]
    payload={'body':'재참여율 22%를 근거로 문제를 정의합니다.','version':p['version'],'operation_id':'same-save'}
    r=client.patch(f'/sessions/{sid}/parts/{p["id"]}',headers=auth,json=payload)
    assert r.status_code==200,r.text
    assert client.patch(f'/sessions/{sid}/parts/{p["id"]}',headers=auth,json=payload).json()==r.json()
    payload['operation_id']='different-save'
    assert client.patch(f'/sessions/{sid}/parts/{p["id"]}',headers=auth,json=payload).status_code==409
    with DB.begin() as db:
        record=db.scalar(select(Record).where(Record.session_id==sid,Record.kind=='part'))
        assert record.actor=='candidate' and record.data['before']['body']==''
    colleague=w['parts'][4]
    assert client.patch(f'/sessions/{sid}/parts/{colleague["id"]}',headers=auth,json={
        'version':1,'operation_id':'forbidden-status','status':'done'}).status_code==403


def test_fixed_events_once_while_ai_job_pending_and_deadline_snapshot():
    sid,auth,_=make_session()
    with DB.begin() as db:
        s=db.get(Session,sid); start=s.started
        tick_session(db,s,start+6601);tick_session(db,s,start+6602)
        assert len(list(db.scalars(select(Event).where(Event.session_id==sid,Event.record_id.is_not(None)))))==7
        assert len([r for r in db.scalars(select(Record).where(Record.session_id==sid)) if r.data.get('event_key')])==7
        tick_session(db,s,start+7201)
        assert s.state=='completed' and elapsed(s)==7200
        snapshot=s.snapshot
    assert client.post('/sessions/'+sid+'/submit',headers=auth).status_code==200
    with DB.begin() as db:
        s=db.get(Session,sid)
        assert s.snapshot==snapshot
        assert len(list(db.scalars(select(Job).where(Job.session_id==sid,Job.flow=='reports'))))==1
    assert client.post('/sessions/'+sid+'/messages',headers=auth,json={
        'flow':'chat','channel':'assistant','text':'late','operation_id':'late'}).status_code==409


def test_job_scope_readonly_chat_late_result_and_model_binding(monkeypatch):
    sid,auth,_=make_session()
    client.post('/sessions/'+sid+'/messages',headers=auth,json={
        'flow':'chat','channel':'assistant','text':'정책을 요약해 주세요.','operation_id':'chat-request'})
    with DB.begin() as db:
        j=db.scalar(select(Job).where(Job.session_id==sid,Job.flow=='chat'));jid=j.id
        original=db.get(Session,sid).binding
    internal=claim(jid)
    other,_,_=make_session()
    r=client.get('/internal/jobs/'+jid+'/context',headers=internal)
    assert r.status_code==200,r.text
    assert all(x['data']['flow']=='chat' for x in r.json()['history'])
    monkeypatch.setenv('LLM_BACKEND','openai')
    assert client.get('/internal/jobs/'+jid+'/context',headers=internal).json()['binding']==original
    assert client.post('/internal/jobs/'+jid+'/business',headers=internal,json={
        'name':'update_part','arguments':{},'operation_id':'attempt'}).status_code==403
    with DB.begin() as db:
        otherjob=db.scalar(select(Job).where(Job.session_id==other))
        otherjid=otherjob.id
    assert client.get('/internal/jobs/'+otherjid+'/context',headers=internal).status_code==403
    assert client.get('/internal/jobs/'+jid+'/documents',headers=internal).status_code==200
    client.post('/sessions/'+sid+'/submit',headers=auth)
    assert client.post('/internal/jobs/'+jid+'/result',headers=internal,json={'text':'late answer'}).json()['status']=='cancelled'


def test_report_save_retry_and_exact_evidence():
    sid,auth,admin=make_session();client.post('/sessions/'+sid+'/submit',headers=auth)
    with DB.begin() as db:
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.flow=='reports'))
    internal=claim(jid)
    assert client.post('/internal/jobs/'+jid+'/result',headers=internal,json={'error':'injected_test_error','retryable':True}).json()['status']=='pending'
    internal=claim(jid)
    snap=client.get('/internal/jobs/'+jid+'/context',headers=internal).json()
    # Synthetic result tests storage/publication, not live model generation.
    import json
    report=assemble_report(json.dumps({'observations': [], 'quality': [dict(item=item, rationale='기획안 본문이 비어 있어 내용 확인이 필요합니다.', supporting_final_ids=[], conflicting_final_ids=[], checks=[]) for item in ['요구사항 반영','근거 활용','실행 가능성']]}), None, snap, evidence_catalog(snap), utilization_links(snap))
    assert client.post('/internal/jobs/'+jid+'/result',headers=internal,json={'report':report}).json()['status']=='done'
    assert client.post('/internal/jobs/'+jid+'/result',headers=internal,json={'report':report}).json()['status']=='done'
    assert client.get('/admin/sessions/'+sid+'/report',headers=admin).json()['state']=='report_ready'


def test_report_binding_is_pinned_separately_from_three_agents(monkeypatch):
    cfg=settings().model_copy(update={'report_llm_backend':'codex_cli','report_model':'gpt-5.6-sol'})
    monkeypatch.setattr('dayzero.backend.main.settings',lambda:cfg)
    from types import SimpleNamespace
    monkeypatch.setattr('dayzero.llm.client.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='codex-cli 0.153.4\n'))
    sid,auth,_=make_session()
    with DB.begin() as db:
        session=db.get(Session,sid)
        original=session.binding;report_binding=session.report_binding
    assert original['backend']=='openai' and original['model']=='gpt-4o-mini'
    assert report_binding['backend']=='codex_cli' and report_binding['model']=='gpt-5.6-sol'
    cfg=cfg.model_copy(update={'report_llm_backend':'openai','report_model':'different-later-setting'})
    assert client.post('/sessions/'+sid+'/submit',headers=auth).status_code==200
    with DB.begin() as db:
        session=db.get(Session,sid);snapshot=session.snapshot
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.flow=='reports'))
    assert snapshot['binding']==original and snapshot['report_binding']==report_binding
    internal=claim(jid)
    assert client.get('/internal/jobs/'+jid+'/context',headers=internal).json()['report_binding']==report_binding


def test_journal_database_is_append_only():
    sid,_,_=make_session()
    with pytest.raises(Exception):
        with DB.begin() as db:
            db.execute(text('UPDATE records SET actor=\'candidate\' WHERE session_id=:sid'),{'sid':sid})


def test_division_three_round_limit_and_operation_deduplication():
    sid,_,_=make_session()
    with DB.begin() as db:
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.agent_id=='colleague'))
    with DB.begin() as db:
        session=db.get(Session,sid);session.started-=610
        for event in db.scalars(select(Event).where(Event.session_id==sid)):
            event.due-=610
        tick_session(db,session)
    auth=claim(jid)
    event_version=client.get('/internal/jobs/'+jid+'/context',headers=auth).json()['event_version']
    base='/internal/jobs/'+jid+'/business'
    owners={'1':'candidate','2':'candidate','3':'shared','4':'colleague','5':'colleague'}
    for number in range(1,4):
        payload={'context_event_version':event_version,'name':'negotiate','operation_id':'division-'+str(number),'arguments':{'owners':owners,'accepted':False}}
        result=client.post(base,headers=auth,json=payload)
        assert result.status_code==200,result.text
        assert {k:result.json()[k] for k in ['rounds','agreed']}=={'rounds':number,'agreed':number==3}
        assert len(result.json()['mutation_ids'])==1
        assert client.post(base,headers=auth,json=payload).json()==result.json()
    assert client.post(base,headers=auth,json={'context_event_version':event_version,'name':'negotiate','operation_id':'division-4',
        'arguments':{'owners':owners,'accepted':True}}).status_code==422


def test_link_expiry_and_finished_link_message():
    admin=company()
    inv=client.post('/admin/invitations',headers=admin,json={'alias':'만료 테스트','email':'expired@example.com'}).json()
    token=urlparse(inv['url']).path.rsplit('/',1)[1]
    with DB.begin() as db:db.get(Session,inv['id']).expires=time.time()-1
    assert client.get('/invitations/'+token).status_code==410
    assert client.post('/auth/login',json={'email':'unknown@example.com','password':'wrong-password'}).status_code==401


def test_debrief_does_not_reveal_future_events_until_submission():
    sid,auth,_=make_session()
    r=client.get('/sessions/'+sid+'/debrief',headers=auth)
    assert r.status_code==409
    assert '멤버십' not in r.text and '도움 요청' not in r.text
    client.post('/sessions/'+sid+'/submit',headers=auth)
    assert len(client.get('/sessions/'+sid+'/debrief',headers=auth).json()['situations'])==4


def test_model_budget_is_shared_between_retries_and_bound_to_flow():
    sid,_,_=make_session()
    with DB.begin() as db:
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.agent_id=='colleague'))
    auth=claim(jid)
    for n in range(1,7):
        r=client.post('/internal/jobs/'+jid+'/model-attempt',headers=auth)
        assert r.status_code==200 and r.json()['attempt']==n
    assert client.post('/internal/jobs/'+jid+'/model-attempt',headers=auth).status_code==429
    client.post('/internal/jobs/'+jid+'/result',headers=auth,json={'error':'injected_network_error','retryable':True})
    auth=claim(jid)
    assert client.post('/internal/jobs/'+jid+'/model-attempt',headers=auth).status_code==429


def test_concurrent_claims_have_one_fenced_lease():
    from concurrent.futures import ThreadPoolExecutor
    sid,_,_=make_session()
    with DB.begin() as db:
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.agent_id=='colleague'))
    def once(_):
        return client.post('/internal/jobs/'+jid+'/claim',headers={'X-Internal-Secret':settings().internal_secret.get_secret_value()})
    with ThreadPoolExecutor(max_workers=6) as executor:responses=list(executor.map(once,range(6)))
    assert sum(r.status_code==200 and not r.json()['terminal'] for r in responses)==1
    assert sum(r.status_code==409 for r in responses)==5


def test_tool_budget_survives_retries_and_deduplicates_exact_operations():
    sid,_,_=make_session()
    with DB.begin() as db:
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.agent_id=='colleague'))
    auth=claim(jid);base='/internal/jobs/'+jid
    first={'operation_id':'tool-0','digest':'a'*64}
    assert client.post(base+'/tool-attempt',headers=auth,json=first).json()['calls']==1
    assert client.post(base+'/tool-attempt',headers=auth,json=first).json()['calls']==1
    assert client.post(base+'/tool-attempt',headers=auth,json={**first,'digest':'b'*64}).status_code==409
    client.post(base+'/result',headers=auth,json={'error':'injected_network_error','retryable':True})
    auth=claim(jid)
    assert client.get(base+'/context',headers=auth).json()['tool_calls']==1
    for n in (1,2):
        assert client.post(base+'/tool-attempt',headers=auth,json={
            'operation_id':'tool-'+str(n),'digest':'a'*64}).json()['calls']==n+1
    assert client.post(base+'/tool-attempt',headers=auth,json={
        'operation_id':'tool-3','digest':'a'*64}).status_code==429


def test_event_change_rejects_stale_business_write_and_reloads_context():
    sid,auth,_=make_session()
    with DB.begin() as db:
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.agent_id=='colleague'))
    job_auth=claim(jid);base='/internal/jobs/'+jid
    ctx=client.get(base+'/context',headers=job_auth).json()
    part=next(p for p in ctx['parts'] if p['number']==4)
    with DB.begin() as db:
        s=db.get(Session,sid)
        tick_session(db,s,s.started+601)
    result=client.post(base+'/business',headers=job_auth,json={
        'name':'update_part','operation_id':'stale-event-write','context_event_version':ctx['event_version'],
        'arguments':{'part_id':part['id'],'change':{'version':part['version'],'body':'outdated draft'}}})
    assert result.status_code==409 and result.json()['detail']=='event_context_changed'
    assert client.get('/sessions/'+sid,headers=auth).json()['parts'][3]['body']==part['body']
    client.post(base+'/result',headers=job_auth,json={'error':'event_context_changed','retryable':True})
    renewed=client.post(base+'/claim',headers={'X-Internal-Secret':settings().internal_secret.get_secret_value()})
    assert renewed.status_code==200 and renewed.json()['resume'] is False


def test_rest_event_receipts_are_scoped_idempotent_and_snapshot_is_immutable():
    sid,auth,_=make_session();other,other_auth,_=make_session()
    ids=[]
    for current in (sid,other):
        with DB.begin() as db:
            s=db.get(Session,current);tick_session(db,s,s.started+601)
            ids.append(db.scalar(select(Event.record_id).where(Event.session_id==current,Event.key=='division')))
    r=client.post('/sessions/'+sid+'/receipts',headers=auth,json={'ids':ids})
    assert r.status_code==200 and r.json()['received']==1
    with DB.begin() as db:
        own=db.scalar(select(Event).where(Event.session_id==sid,Event.key=='division')).received_at
        assert own is not None
        assert db.scalar(select(Event).where(Event.session_id==other,Event.key=='division')).received_at is None
    assert client.post('/sessions/'+sid+'/receipts',headers=other_auth,json={'ids':ids}).status_code==404
    assert client.post('/sessions/'+sid+'/receipts',headers=auth,json={'ids':ids}).json()['received']==0
    client.post('/sessions/'+sid+'/submit',headers=auth)
    with DB.begin() as db:snapshot=db.get(Session,sid).snapshot
    client.post('/sessions/'+sid+'/receipts',headers=auth,json={'ids':ids})
    with DB.begin() as db:
        assert db.get(Session,sid).snapshot==snapshot
        assert db.scalar(select(Event).where(Event.session_id==sid,Event.key=='division')).received_at==own


def test_concurrent_submission_has_one_snapshot_and_report_job():
    from concurrent.futures import ThreadPoolExecutor
    sid,auth,_=make_session()
    with ThreadPoolExecutor(max_workers=6) as executor:
        responses=list(executor.map(lambda _:client.post('/sessions/'+sid+'/submit',headers=auth),range(6)))
    assert all(r.status_code==200 for r in responses)
    with DB.begin() as db:
        assert len(list(db.scalars(select(Job).where(Job.session_id==sid,Job.flow=='reports'))))==1
        assert len(list(db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='submit'))))==1
        assert db.get(Session,sid).snapshot['scenario_version']=='looplab-v1'


def test_user_operation_ids_cannot_replace_scripted_events():
    sid,auth,_=make_session()
    sent=client.post('/sessions/'+sid+'/messages',headers=auth,json={
        'flow':'collaboration','channel':'team','text':'지원자 메시지','operation_id':'event:change'})
    assert sent.status_code==200
    with DB.begin() as db:
        s=db.get(Session,sid);tick_session(db,s,s.started+4201)
        event=db.scalar(select(Event).where(Event.session_id==sid,Event.key=='change'))
        r=db.get(Record,event.record_id)
        assert r.actor=='leader' and '3개월' in r.data['text']
        assert r.id != sent.json()['id']


def test_queued_request_context_excludes_later_candidate_messages():
    sid,auth,_=make_session()
    for op,text_value in [('first','첫 번째 요청'),('later','아직 처리 순서가 아닌 다음 요청')]:
        assert client.post('/sessions/'+sid+'/messages',headers=auth,json={
            'flow':'chat','channel':'assistant','text':text_value,'operation_id':op}).status_code==200
    with DB.begin() as db:
        jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.flow=='chat').order_by(Job.sequence).limit(1))
    scoped=claim(jid)
    ctx=client.get('/internal/jobs/'+jid+'/context',headers=scoped).json()
    assert [r['data']['text'] for r in ctx['history']]==['첫 번째 요청']
