"""New synthetic sessions, actual Postgres. No model generation is implied."""
import time
from sqlalchemy import select
from test_backend import client, make_session, claim
from dayzero.backend.db import DB, Job, Session, Record, AgentMemory
from dayzero.backend.domain import tick_session
from dayzero.backend.security import decode, issue


def agent_job(sid, agent):
    with DB.begin() as db:
        return db.scalar(select(Job.id).where(Job.session_id==sid, Job.agent_id==agent).order_by(Job.sequence))


def post_message(sid, auth, channel='team', **extra):
    return client.post('/sessions/'+sid+'/messages',headers=auth,json={
        'flow':'chat' if channel=='assistant' else 'collaboration', 'channel':channel,
        'text':'합성 업무 요청', 'operation_id':str(time.time_ns()), **extra})


def test_routing_independent_sequences_and_signed_identity():
    sid,auth,_=make_session()
    with DB.begin() as db:
        jobs=list(db.scalars(select(Job).where(Job.session_id==sid)))
        assert {(j.agent_id,j.sequence) for j in jobs}=={('leader',1),('colleague',1)}
    r=post_message(sid,auth,recipients=['leader','colleague'])
    assert r.status_code==200
    with DB.begin() as db:
        assert {(j.agent_id,j.sequence) for j in db.scalars(select(Job).where(Job.input_id==r.json()['id']))}=={('leader',2),('colleague',2)}
    assert post_message(sid,auth,'dm',recipients=['leader']).status_code==422
    assert post_message(sid,auth,'assistant',recipients=['colleague']).status_code==422
    jid=agent_job(sid,'leader');scoped=claim(jid)
    claims=decode(scoped['Authorization'][7:],'job')
    forged=issue(jid,claims['company_id'],'job',lease_id=claims['lease_id'],agent_id='colleague',role_version='agents-v1')
    assert client.get('/internal/jobs/'+jid+'/context',headers={'Authorization':'Bearer '+forged}).status_code==403


def test_private_histories_memory_and_late_hour_roles():
    sid,auth,_=make_session()
    with DB.begin() as db:
        for job in db.scalars(select(Job).where(Job.session_id==sid)):job.status='cancelled'
    dm=post_message(sid,auth,'dm',text='DM 전용 비공유 내용').json()
    ai=post_message(sid,auth,'assistant',text='개인 AI 비공유 내용').json()
    team=post_message(sid,auth,text='팀 공통 공개 내용',recipients=['leader','colleague']).json()
    with DB.begin() as db:
        ids={agent:db.scalar(select(Job.id).where(Job.session_id==sid,Job.agent_id==agent,Job.status=='pending').order_by(Job.sequence)) for agent in ['leader','colleague','chat']}
    scoped={agent:claim(ids[agent]) for agent in ['leader','colleague','chat']}
    contexts={agent:client.get('/internal/jobs/'+ids[agent]+'/context',headers=h).json() for agent,h in scoped.items()}
    assert dm['id'] in [r['id'] for r in contexts['colleague']['history']]
    assert team['id'] in [r['id'] for r in contexts['leader']['history']]
    assert dm['id'] not in [r['id'] for r in contexts['leader']['history']]
    assert ai['id'] not in [r['id'] for r in contexts['colleague']['history']]
    assert all(r['data']['flow']=='chat' for r in contexts['chat']['history'])
    leader=ids['leader']
    bad=client.post('/internal/jobs/'+leader+'/business',headers=scoped['leader'],json={
        'name':'remember','arguments':{'source_id':dm['id'],'note':'secret'},'operation_id':'steal','context_event_version':0})
    assert bad.status_code==403
    for agent in ['leader','chat']:
        jid=ids[agent]
        assert client.post('/internal/jobs/'+jid+'/business',headers=scoped[agent],json={
            'name':'create_task','arguments':{},'operation_id':'write'}).status_code==403
    with DB.begin() as db:
        s=db.get(Session,sid);s.started=time.time()-6610;tick_session(db,s)
    colleague=ids['colleague'];ctx=client.get('/internal/jobs/'+colleague+'/context',headers=scoped['colleague']).json()
    wrong=client.post('/internal/jobs/'+colleague+'/result',headers=scoped['colleague'],json={
        'agent_id':'leader','text':'사칭 응답','context_event_version':ctx['event_version']})
    assert wrong.status_code==403
    saved=client.post('/internal/jobs/'+colleague+'/result',headers=scoped['colleague'],json={
        'agent_id':'colleague','text':'동료의 현재 업무 질문입니다.','context_event_version':ctx['event_version']})
    assert saved.status_code==200,saved.text
    w=client.get('/sessions/'+sid,headers=auth).json()
    assert next(r for r in w['records'] if r['data'].get('job_id')==colleague)['actor']=='colleague'
    with DB.begin() as db:
        memories=list(db.scalars(select(AgentMemory).where(AgentMemory.session_id==sid)))
        assert len(memories)==3
        assert db.get(AgentMemory,(sid,'leader')).data=={}
        assert db.get(AgentMemory,(sid,'colleague')).data['last_job_id']==colleague


def test_reply_and_mentions_route_to_fixed_person():
    sid,auth,_=make_session();w=client.get('/sessions/'+sid,headers=auth).json()
    leader=next(r for r in w['records'] if r['actor']=='leader')
    assert post_message(sid,auth,reply_to=leader['id']).json()['data']['recipients']==['leader']
    assert post_message(sid,auth,text='@팀장 진행을 보고합니다').json()['data']['recipients']==['leader']
    assert post_message(sid,auth,text='@팀장 @동료 함께 확인해 주세요').json()['data']['recipients']==['leader','colleague']
    assert post_message(sid,auth,agent_id='leader').status_code==422


def test_reassignment_needs_confirmation_and_keeps_exact_operation_proof():
    sid,auth,_=make_session();jid=agent_job(sid,'colleague');scoped=claim(jid)
    owners={'1':'candidate','2':'candidate','3':'shared','4':'colleague','5':'colleague'}
    base='/internal/jobs/'+jid+'/business'
    first={'name':'negotiate','operation_id':'initial','context_event_version':0,
        'arguments':{'owners':owners,'accepted':True}}
    accepted=client.post(base,headers=scoped,json=first)
    assert accepted.status_code==200,accepted.text
    source=post_message(sid,auth,text='4번도 제가 맡겠습니다. 변경을 합의해 주세요.').json()
    change={'name':'negotiate','operation_id':'change','context_event_version':0,
        'arguments':{'owners':{**owners,'4':'candidate'},'accepted':False,'source_id':source['id']}}
    denied=client.post(base,headers=scoped,json=change)
    assert denied.status_code==409 and denied.json()['detail']=='division_confirmation_required'
    assert client.get('/sessions/'+sid,headers=auth).json()['parts'][3]['owner']=='colleague'
    change['arguments']['accepted']=True
    changed=client.post(base,headers=scoped,json=change)
    assert changed.status_code==200,changed.text
    assert client.get('/sessions/'+sid,headers=auth).json()['parts'][3]['owner']=='candidate'
    assert client.post(base,headers=scoped,json=first).json()==accepted.json()
    assert set(client.get('/internal/jobs/'+jid+'/context',headers=scoped).json()['mutation_ids'])==(
        set(accepted.json()['mutation_ids']) | set(changed.json()['mutation_ids']))
    changed_input={**first,'arguments':{**first['arguments'],'owners':{**owners,'4':'candidate'}}}
    assert client.post(base,headers=scoped,json=changed_input).status_code==409


def test_free_tasks_sources_versions_completion_and_deduplication():
    sid,auth,_=make_session();w=client.get('/sessions/'+sid,headers=auth).json();part=w['parts'][3]
    source=post_message(sid,auth,text='이도현님, 비교 기준을 4번 파트에 정리해 주세요.').json()
    payload={'title':'사례 비교 기준','owner':'colleague','part_id':part['id'],'source_id':source['id'],'note':'가상의 사례임을 명시','operation_id':'assign'}
    task=client.post('/sessions/'+sid+'/tasks',headers=auth,json=payload)
    assert task.status_code==200,task.text
    assert client.post('/sessions/'+sid+'/tasks',headers=auth,json=payload).json()==task.json()
    assert client.post('/sessions/'+sid+'/tasks',headers=auth,json={**payload,'title':'collision'}).status_code==409
    task=task.json();jid=agent_job(sid,'colleague');scoped=claim(jid)
    rejected=client.post('/internal/jobs/'+jid+'/business',headers=scoped,json={
        'name':'update_task','operation_id':'cannot-skip-document','context_event_version':0,
        'arguments':{'task_id':task['id'],'change':{'version':task['version'],'status':'done'}}})
    assert rejected.status_code==409 and rejected.json()['detail']=='complete_task_required'
    body={'name':'complete_task','operation_id':'complete','context_event_version':0,'arguments':{
        'task_id':task['id'],'version':task['version'],'part_version':part['version'],
        'body':'가상의 비교 사례를 알림·동행 기준으로 정리합니다. 실제 서비스 근거는 추가 확인합니다.','note':'완료'}}
    saved=client.post('/internal/jobs/'+jid+'/business',headers=scoped,json=body)
    assert saved.status_code==200,saved.text
    assert client.post('/internal/jobs/'+jid+'/business',headers=scoped,json=body).json()==saved.json()
    w=client.get('/sessions/'+sid,headers=auth).json()
    assert len(w['parts'])==5 and len(w['tasks'])==1 and w['tasks'][0]['status']=='done'
    assert w['parts'][3]['version']==part['version']+1
    assert len(saved.json()['mutation_ids'])==2
    assert set(client.get('/internal/jobs/'+jid+'/context',headers=scoped).json()['mutation_ids'])==set(saved.json()['mutation_ids'])
    leader=agent_job(sid,'leader');leader_auth=claim(leader)
    assert client.get('/internal/jobs/'+leader+'/context',headers=leader_auth).json()['mutation_ids']==[]
    assert client.patch('/sessions/'+sid+'/tasks/'+task['id'],headers=auth,json={'version':1,'operation_id':'stale','status':'todo'}).status_code==409
    client.post('/sessions/'+sid+'/submit',headers=auth)
    assert client.post('/sessions/'+sid+'/tasks',headers=auth,json={**payload,'operation_id':'late'}).status_code==409
    with DB.begin() as db:
        snap=db.get(Session,sid).snapshot
        assert snap['version']==2 and snap['tasks'][0]['status']=='done'
        assert snap['contract']['roles']=='agents-v1'


def test_promise_and_assignment_triggers_once_and_review_does_not_overwrite():
    sid,auth,_=make_session();w=client.get('/sessions/'+sid,headers=auth).json()
    part=w['parts'][2]
    client.patch('/sessions/'+sid+'/parts/'+part['id'],headers=auth,json={'version':1,'body':'지원자의 무료 기능 최종안','operation_id':'draft'})
    task=client.post('/sessions/'+sid+'/tasks',headers=auth,json={'title':'나중에 검토','owner':'colleague','part_id':part['id'],'operation_id':'t'}).json()
    changed=client.patch('/sessions/'+sid+'/tasks/'+task['id'],headers=auth,json={
        'version':1,'status':'waiting','due_elapsed':120,'operation_id':'promise'})
    assert changed.status_code==200,changed.text
    with DB.begin() as db:
        s=db.get(Session,sid)
        for offset in [121,122,5401,5402]:tick_session(db,s,s.started+offset)
        triggers=list(db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='work_trigger')))
        assert sum(r.data['trigger'].startswith('promise:') for r in triggers)==1
        assert len(list(db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='review_draft'))))==1
    w=client.get('/sessions/'+sid,headers=auth).json()
    assert w['parts'][2]['body']=='지원자의 무료 기능 최종안' and w['parts'][2]['version']==2


def test_task_created_waiting_is_not_assigned_before_its_promise():
    sid,auth,_=make_session()
    payload={'title':'10분 뒤 비교','owner':'colleague','status':'waiting','due_elapsed':600,'operation_id':'future'}
    response=client.post('/sessions/'+sid+'/tasks',headers=auth,json=payload)
    assert response.status_code==200,response.text
    task=response.json()
    assert task['status']=='waiting' and task['due_elapsed']==600
    assert client.post('/sessions/'+sid+'/tasks',headers=auth,json=payload).json()==task
    with DB.begin() as db:
        session=db.get(Session,sid)
        tick_session(db,session,session.started+599)
        assert not list(db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='work_trigger')))
        tick_session(db,session,session.started+601)
        tick_session(db,session,session.started+602)
        triggers=list(db.scalars(select(Record).where(Record.session_id==sid,Record.kind=='work_trigger')))
        assert len(triggers)==1 and triggers[0].data['trigger'].startswith('promise:')
    for due in [None,0]:
        rejected=client.post('/sessions/'+sid+'/tasks',headers=auth,json={**payload,'operation_id':'bad-'+str(due),'due_elapsed':due})
        assert rejected.status_code==422 and rejected.json()['detail']=='future_promise_required'


def test_relative_promise_is_computed_once_and_replay_keeps_original_time():
    sid,auth,_=make_session()
    payload={'title':'10분 뒤 비교','owner':'colleague','delay_seconds':600,'operation_id':'relative'}
    response=client.post('/sessions/'+sid+'/tasks',headers=auth,json=payload)
    assert response.status_code==200,response.text
    task=response.json()
    assert task['status']=='waiting' and 600<=task['due_elapsed']<610
    with DB.begin() as db:db.get(Session,sid).started-=60
    assert client.post('/sessions/'+sid+'/tasks',headers=auth,json=payload).json()==task
    bad=client.post('/sessions/'+sid+'/tasks',headers=auth,json={**payload,'operation_id':'ambiguous','due_elapsed':700})
    assert bad.status_code==422 and bad.json()['detail']=='ambiguous_promise_time'


def test_read_receipt_is_separate_from_delivery():
    sid,auth,_=make_session()
    with DB.begin() as db:
        s=db.get(Session,sid);tick_session(db,s,s.started+601)
    w=client.get('/sessions/'+sid,headers=auth).json();mid=next(r['id'] for r in w['records'] if r['data'].get('event_key')=='division')
    client.post('/sessions/'+sid+'/receipts',headers=auth,json={'ids':[mid]})
    assert not any(r['kind']=='read' for r in client.get('/sessions/'+sid,headers=auth).json()['records'])
    for _ in range(2):assert client.post('/sessions/'+sid+'/reads',headers=auth,json={'ids':[mid]}).status_code==200
    assert len([r for r in client.get('/sessions/'+sid,headers=auth).json()['records'] if r['kind']=='read'])==1


def test_two_different_task_paths_keep_same_final_criteria_and_different_contributions(monkeypatch):
    import json
    from datetime import datetime
    from dayzero.reporting.build import assemble_report,evidence_catalog,utilization_links
    from dayzero.backend.db import Part
    now=[time.time()]
    class ClockDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return cls.fromtimestamp(now[0],tz)
    monkeypatch.setattr('jwt.api_jwt.datetime',ClockDateTime)
    monkeypatch.setattr('dayzero.backend.domain.time.time',lambda:now[0])
    reports=[]
    for path in ['candidate-first','colleague-first']:
        sid,auth,_=make_session();start=now[0]
        with DB.begin() as db:
            for job in db.scalars(select(Job).where(Job.session_id==sid)):job.status='cancelled'
        w=client.get('/sessions/'+sid,headers=auth).json()
        source=post_message(sid,auth,text='4번 사례를 맡아 주세요. 가상의 사례임을 표시해 주세요.').json()
        count=1 if path=='candidate-first' else 3
        for i in range(count):
            created=client.post('/sessions/'+sid+'/tasks',headers=auth,json={
                'title':path+'-'+str(i),'owner':'candidate' if path=='candidate-first' else 'colleague',
                'part_id':w['parts'][3]['id'],'source_id':source['id'],'operation_id':'task-'+str(i)})
            assert created.status_code==200,created.text
        if path=='colleague-first':
            with DB.begin() as db:jid=db.scalar(select(Job.id).where(Job.session_id==sid,Job.input_id==source['id'],Job.agent_id=='colleague'))
            scoped=claim(jid)
        for p in reversed(w['parts']) if path=='colleague-first' else w['parts']:
            body='무료 알림 실험: 대상과 정책·근거·담당·일정·측정 가정을 확인합니다. 파트 '+str(p['number'])
            if path=='colleague-first' and p['number']==4:
                r=client.post('/internal/jobs/'+jid+'/business',headers=scoped,json={'name':'update_part','arguments':{
                    'part_id':p['id'],'change':{'version':p['version'],'body':body}},'operation_id':'colleague-write','context_event_version':0})
            else:
                r=client.patch('/sessions/'+sid+'/parts/'+p['id'],headers=auth,json={'version':p['version'],'body':body,'operation_id':'p'+p['id']})
            assert r.status_code==200,r.text
        now[0]=start+70
        client.post('/sessions/'+sid+'/submit',headers=auth)
        with DB.begin() as db:snap=db.get(Session,sid).snapshot
        catalog=evidence_catalog(snap);primary=next(r['id'] for r in snap['records'] if r['kind']=='part' and r['actor']=='candidate')
        output={'observations':[{'item':'요구사항 반영','observation':'무료 알림 실험의 조건을 문서에 저장함','candidate_ids':[primary],'supporting_ids':[]}],
            'quality':[{'item':item,'rationale':'최종본의 조건과 실행 가정을 원문으로 함께 확인합니다.',
                'supporting_final_ids':['final:'+p['id']+':v'+str(p['version']) for p in snap['parts']],
                'conflicting_final_ids':[],'checks':['의미와 근거의 적용 범위 확인']} for item in ['요구사항 반영','근거 활용','실행 가능성']]}
        reports.append(assemble_report(json.dumps(output),None,snap,catalog,utilization_links(snap)))
    a,b=reports
    assert [p['body'] for p in a['final_parts']]==[p['body'] for p in b['final_parts']]
    assert a['contract']==b['contract'] and a['conditions']['target']==b['conditions']['target']
    assert len(a['tasks'])==1 and len(b['tasks'])==3
    assert a['quality_comparison'][0]['supporting'][3]['actor']=='candidate'
    assert b['quality_comparison'][0]['supporting'][3]['actor']=='colleague'
