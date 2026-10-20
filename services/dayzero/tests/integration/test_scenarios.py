"""Actual DB/API scenario contracts; task tool results are synthetic, not live model claims."""
import time
from urllib.parse import urlsplit
from sqlalchemy import select
from test_backend import client, company, claim
from dayzero.backend.db import DB, Job, Session
from dayzero.reporting.build import session_conditions


def scenario_session(scenario_id, admin=None):
    admin = admin or company()
    response = client.post('/admin/invitations', headers=admin, json={
        'alias':'다중 시나리오 합성 검증','email':'synthetic@example.com','scenario_id':scenario_id})
    assert response.status_code==200, response.text
    sid = response.json()['id']
    token = urlsplit(response.json()['url']).path.rsplit('/',1)[1]
    auth = {'Authorization':'Bearer '+token}
    assert client.post('/sessions/'+sid+'/consent', headers=auth, json={'accepted':True}).status_code==200
    assert client.post('/sessions/'+sid+'/start', headers=auth).status_code==200
    return sid,auth,admin


def test_selected_scenario_drives_sections_people_and_documents_without_leaks():
    admin = company()
    a,aa,_ = scenario_session('looplab-v2',admin)
    b,ba,_ = scenario_session('teamnote-onboarding-v1',admin)
    first = client.get('/sessions/'+a,headers=aa).json()
    second = client.get('/sessions/'+b,headers=ba).json()
    assert len(first['parts'])==5 and len(second['parts'])==3
    assert not first['tasks'] and not second['tasks']
    assert all(p['owner']=='shared' for p in second['parts'])
    assert second['scenario']['roles']['colleague']['name']=='김민서'
    assert 'scenario_definition' not in second['contract']
    assert '초대를 받아 처음 참여한 팀 관리자' not in str(second)
    docs = client.get('/sessions/'+b+'/documents',headers=ba).json()
    assert len(docs)==3 and all('teamnote-onboarding-v1--' in d['id'] for d in docs)
    foreign = client.get('/sessions/'+a+'/documents',headers=aa).json()[0]['id']
    assert client.get('/sessions/'+b+'/documents/'+foreign,headers=ba).status_code==404
    assert client.get('/sessions/'+b+'/documents/'+docs[0]['id'],headers=ba).status_code==200
    assert client.post('/admin/invitations',headers=admin,json={
        'alias':'bad','email':'synthetic@example.com','scenario_id':'missing-v1'}).status_code==422


def test_agreement_creates_one_task_and_actual_document_completion_in_three_section_scenario():
    sid,auth,_ = scenario_session('teamnote-onboarding-v1')
    with DB.begin() as db:
        for job in db.scalars(select(Job).where(Job.session_id==sid)):
            job.status='cancelled'
    source = client.post('/sessions/'+sid+'/messages',headers=auth,json={
        'flow':'collaboration','channel':'team','recipients':['colleague'],
        'text':'김민서님은 2번 개선안을 맡아 주세요. 정책을 확인해 초안을 작성해 주세요.',
        'operation_id':'agreement'}).json()
    with DB.begin() as db:
        jid = db.scalar(select(Job.id).where(Job.input_id==source['id']))
    scoped = claim(jid)
    base = '/internal/jobs/'+jid+'/business'
    workspace = client.get('/sessions/'+sid,headers=auth).json()
    owners = {'1':'candidate','2':'colleague','3':'shared'}
    division = {'name':'negotiate','operation_id':'three-part-division','context_event_version':0,
        'arguments':{'owners':owners,'accepted':True,'source_id':source['id']}}
    assert client.post(base,headers=scoped,json=division).status_code==200
    workspace = client.get('/sessions/'+sid,headers=auth).json()
    part = workspace['parts'][1]
    create = {'name':'create_task','operation_id':'record-agreement','context_event_version':0,
        'arguments':{'title':'정책을 반영한 온보딩 초안','owner':'colleague','part_id':part['id'],
                     'source_id':source['id'],'note':'대화에서 합의한 2번 항목 초안'}}
    response = client.post(base,headers=scoped,json=create)
    assert response.status_code==200,response.text
    task = response.json()['task']
    assert client.post(base,headers=scoped,json=create).json()==response.json()
    complete = client.post(base,headers=scoped,json={'name':'complete_task','operation_id':'finish-agreement',
        'context_event_version':0,'arguments':{'task_id':task['id'],'version':task['version'],
        'part_version':part['version'],'body':'제품 안의 시작 가이드와 예시 문서를 제공합니다. 개인 연락처는 수집하지 않습니다.',
        'note':'합의한 초안을 공유 문서에 저장했습니다.'}})
    assert complete.status_code==200,complete.text
    saved = client.get('/sessions/'+sid,headers=auth).json()
    assert len(saved['tasks'])==1 and saved['tasks'][0]['status']=='done'
    assert saved['tasks'][0]['source_id']==source['id']
    assert saved['parts'][1]['body'].startswith('제품 안의 시작 가이드')
    mention = client.post('/sessions/'+sid+'/messages',headers=auth,json={
        'flow':'collaboration','channel':'team','text':'@박지우 진행 사항을 공유합니다.','operation_id':'dynamic-mention'})
    assert mention.json()['data']['recipients']==['leader']


def test_report_target_and_debrief_follow_delivered_scenario_conditions():
    sid,auth,_ = scenario_session('teamnote-onboarding-v1')
    with DB.begin() as db:
        db.get(Session,sid).started=time.time()-4201
    workspace = client.get('/sessions/'+sid,headers=auth).json()
    change = next(r for r in workspace['records'] if r['data'].get('event_key')=='change')
    assert client.post('/sessions/'+sid+'/receipts',headers=auth,json={'ids':[change['id']]}).status_code==200
    assert client.post('/sessions/'+sid+'/submit',headers=auth).status_code==200
    with DB.begin() as db:
        snapshot=db.get(Session,sid).snapshot
        assert 'scenario_definition' not in snapshot['contract']
        assert session_conditions(snapshot)['target']=='초대를 받아 처음 참여한 팀 관리자'
        assert all('teamnote-onboarding-v1--' in d['id'] for d in snapshot['documents'])
    debrief = client.get('/sessions/'+sid+'/debrief',headers=auth).json()
    assert any('팀 관리자' in s['text'] for s in debrief['situations'])
    assert not any('멤버십' in s['text'] for s in debrief['situations'])
