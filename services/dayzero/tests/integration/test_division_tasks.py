"""Regression checks for automatic division cards, using isolated database sessions."""
import pytest
from sqlalchemy import select
from test_backend import client, make_session, claim
from test_agents_tasks import agent_job, post_message
from dayzero.backend.db import DB, Job, Part, Task, Record


def agreement():
    sid, auth, _ = make_session()
    source = post_message(sid, auth, text='4번은 맡아 주세요. 나머지는 제가 진행할게요.').json()
    jid = agent_job(sid, 'colleague')
    scoped = claim(jid)
    owners = {str(n): 'colleague' if n == 4 else 'candidate' for n in range(1, 6)}
    payload = {'name': 'negotiate', 'operation_id': 'division_%', 'context_event_version': 0,
               'arguments': {'owners': owners, 'accepted': True, 'source_id': source['id']}}
    base = '/internal/jobs/'+jid+'/business'
    result = client.post(base, headers=scoped, json=payload)
    assert result.status_code == 200, result.text
    workspace = client.get('/sessions/'+sid, headers=auth).json()
    return sid, auth, scoped, base, source, payload, result.json(), workspace['tasks'][0]


@pytest.mark.parametrize('change', [
    {'delay_seconds': 600},
    {'owner': 'candidate'},
    {'different_source': True},
])
def test_new_task_request_keeps_explicit_timing_owner_and_source(change):
    sid, auth, scoped, base, source, _, _, automatic = agreement()
    args = {'title': '후속 검토', 'owner': 'colleague', 'part_id': automatic['part_id'],
            'source_id': source['id'], **change}
    if args.pop('different_source', False):
        args['source_id'] = post_message(sid, auth, text='추가로 검토해 주세요.').json()['id']
    response = client.post(base, headers=scoped, json={
        'name': 'create_task', 'operation_id': 'explicit-followup', 'context_event_version': 0,
        'arguments': args})
    assert response.status_code == 200, response.text
    task = response.json()['task']
    assert task['id'] != automatic['id'] and task['owner'] == args['owner']
    assert task['source_id'] == args['source_id']
    if 'delay_seconds' in args:
        assert task['status'] == 'waiting' and task['due_elapsed'] >= 600
    assert len(client.get('/sessions/'+sid, headers=auth).json()['tasks']) == 2


def test_existing_automatic_card_does_not_bypass_promise_validation():
    _, _, scoped, base, source, _, _, automatic = agreement()
    response = client.post(base, headers=scoped, json={
        'name': 'create_task', 'operation_id': 'invalid-wait', 'context_event_version': 0,
        'arguments': {'title': '나중에 검토', 'owner': 'colleague', 'part_id': automatic['part_id'],
                      'source_id': source['id'], 'status': 'waiting'}})
    assert response.status_code == 422 and response.json()['detail'] == 'future_promise_required'


@pytest.mark.parametrize('new_owner', ['candidate', 'shared'])
def test_reassignment_cancels_only_automatic_card_and_pending_work(new_owner):
    sid, auth, scoped, base, source, payload, _, automatic = agreement()
    manual = client.post('/sessions/'+sid+'/tasks', headers=auth, json={
        'title': '명시적으로 요청한 별도 검토', 'owner': 'colleague', 'part_id': automatic['part_id'],
        'source_id': source['id'], 'operation_id': 'manual-review'}).json()
    payload = {**payload, 'operation_id': 'reassign',
        'arguments': {**payload['arguments'], 'owners': {**payload['arguments']['owners'], '4': new_owner}}}
    response = client.post(base, headers=scoped, json=payload)
    assert response.status_code == 200, response.text
    assert len(response.json()['mutation_ids']) == 2
    assert client.post(base, headers=scoped, json=payload).json() == response.json()
    with DB.begin() as db:
        assert db.get(Task, automatic['id']).status == 'cancelled'
        assert db.get(Task, manual['id']).status == 'todo'
        for task, expected in [(automatic, 'cancelled'), (manual, 'pending')]:
            trigger = db.scalar(select(Record.id).where(Record.session_id == sid,
                Record.kind == 'work_trigger', Record.data['target']['id'].astext == task['id']))
            assert db.scalar(select(Job.status).where(Job.input_id == trigger)) == expected


@pytest.mark.parametrize('completed_by', [None, 'part', 'task'])
def test_partial_draft_is_assigned_but_completed_work_is_not(completed_by):
    sid, auth, _ = make_session()
    source = post_message(sid, auth, text='4번 초안을 이어서 정리해 주세요.').json()
    jid = agent_job(sid, 'colleague')
    scoped = claim(jid)
    with DB.begin() as db:
        part = db.scalar(select(Part).where(Part.session_id == sid, Part.number == 4))
        part.body = '작성 중인 초안'
        if completed_by == 'part':
            part.status = 'done'
        if completed_by == 'task':
            db.add(Task(session_id=sid, title='완료한 초안', owner='colleague', part_id=part.id, status='done'))
    response = client.post('/internal/jobs/'+jid+'/business', headers=scoped, json={
        'name': 'negotiate', 'operation_id': 'draft-division', 'context_event_version': 0,
        'arguments': {'owners': {str(n): 'colleague' if n == 4 else 'candidate' for n in range(1, 6)},
                      'accepted': True, 'source_id': source['id']}})
    assert response.status_code == 200, response.text
    tasks = client.get('/sessions/'+sid, headers=auth).json()['tasks']
    assert len([t for t in tasks if t['status'] == 'todo']) == (0 if completed_by else 1)


def test_division_replay_uses_exact_operation_not_sql_wildcards():
    _, _, scoped, base, _, payload, first, _ = agreement()
    second = {**payload, 'operation_id': 'division_other',
        'arguments': {**payload['arguments'], 'owners': {**payload['arguments']['owners'], '5': 'colleague'}}}
    response = client.post(base, headers=scoped, json=second)
    assert response.status_code == 200, response.text
    assert client.post(base, headers=scoped, json=payload).json() == first
