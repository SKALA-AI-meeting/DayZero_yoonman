import hashlib
import time
from fastapi import HTTPException
from sqlalchemy import select, func
from dayzero.backend.db import Record, Part, Event, Job, Task, AgentMemory
from dayzero.shared.scenarios import load_scenario, session_scenario, public_scenario, public_contract

LEGACY = load_scenario('looplab-v1')
PARTS = [p['title'] for p in LEGACY['sections']]
EVENTS = [(e['key'], e['offset'], e['actor'], e['text']) for e in LEGACY['events']]
BRIEF = LEGACY['brief']
EVALUATION_NOTICE = '업무 수행 과정과 협업 기록은 종료 후 근거 중심으로 정리되며, 최종 판단은 사람이 합니다. 지금은 과제에 집중해 주세요.'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def elapsed(session, now=None):
    return max(0, min(7200, (session.ended or now or time.time()) - (session.started or time.time())))


def require_active(session):
    if session.state != 'active' or elapsed(session) >= 7200:
        raise HTTPException(409, 'session_closed')


def journal(db, session, kind, actor, data, operation_id, now=None):
    existing = db.scalar(select(Record).where(Record.session_id == session.id,
                                             Record.operation_id == operation_id))
    if existing:
        return existing
    record = Record(session_id=session.id, kind=kind, actor=actor, data=data,
                    operation_id=operation_id, at=now or time.time(), elapsed=elapsed(session, now))
    db.add(record)
    db.flush()
    return record


AGENTS = ('leader', 'colleague', 'chat')
ROLE_VERSION = 'agents-v1'
CONTRACT = {'version': 'dayzero-v2', 'roles': ROLE_VERSION, 'permissions': 'permissions-v1',
    'quality': 'quality-v1', 'duration_seconds': 7200, 'tool_limit': 3,
    'initial_division_limit': 3, 'quality_criteria': ['요구사항 반영', '근거 활용', '실행 가능성']}


def enqueue(db, session, agent_id, input_id):
    if agent_id not in (*AGENTS, 'reports'):
        raise HTTPException(422, 'invalid_agent')
    existing = db.scalar(select(Job).where(Job.session_id == session.id,
        Job.agent_id == agent_id, Job.input_id == input_id))
    if existing:
        return existing
    sequence = db.scalar(select(func.coalesce(func.max(Job.sequence), 0)).where(
        Job.session_id == session.id, Job.agent_id == agent_id)) + 1
    flow = 'reports' if agent_id == 'reports' else 'chat' if agent_id == 'chat' else 'collaboration'
    job = Job(session_id=session.id, flow=flow, agent_id=agent_id, role_version=ROLE_VERSION,
              input_id=input_id, sequence=sequence)
    db.add(job)
    db.flush()
    return job


def trigger_work(db, session, key, target, now=None):
    r = journal(db, session, 'work_trigger', 'system',
        {'text': '업무 계기: '+key, 'trigger': key, 'target': target,
         'flow': 'collaboration', 'channel': 'team'}, 'trigger:'+key, now)
    return enqueue(db, session, 'colleague', r.id)


def tasks(db, sid):
    return db.scalars(select(Task).where(Task.session_id == sid).order_by(Task.id)).all()


def serialize_task(task):
    return {k: getattr(task, k) for k in ['id', 'title', 'owner', 'status', 'part_id',
        'source_id', 'note', 'due_elapsed', 'version']}


def serialize_record(r):
    return {k: getattr(r, k) for k in ['id', 'kind', 'actor', 'at', 'elapsed', 'data']}


def serialize_part(p):
    return {k: getattr(p, k) for k in ['id', 'number', 'title', 'body', 'owner', 'status', 'version']}


def records(db, sid):
    return db.scalars(select(Record).where(Record.session_id == sid).order_by(Record.at, Record.id)).all()


def parts(db, sid):
    return db.scalars(select(Part).where(Part.session_id == sid).order_by(Part.number)).all()


def close_session(db, session, now=None):
    if session.state != 'active':
        return
    session.ended = min(now or time.time(), session.started + 7200)
    session.state = 'completed'
    journal(db, session, 'submit', 'system', {'reason': 'deadline' if elapsed(session) >= 7200 else 'manual'}, 'submit')
    from dayzero.rag.documents import visible_documents
    session.snapshot = {'scenario_version': session.scenario_version, 'documents': visible_documents(db, session.company_id, elapsed(session), session_scenario(session)), 'version': 2, 'contract': public_contract(session.contract), 'scenario': {**public_scenario(session_scenario(session), elapsed(session)),
        'changed_target': session_scenario(session)['changed_target'] if any(e.key=='change' and e.received_at for e in db.scalars(select(Event).where(Event.session_id==session.id))) else None}, 'tasks': [serialize_task(t) for t in tasks(db, session.id)], 'session_id': session.id, 'binding': session.binding,
        'report_binding': session.report_binding or session.binding,
        'started': session.started, 'ended': session.ended,
        'records': [serialize_record(r) for r in records(db, session.id)],
        'parts': [serialize_part(p) for p in parts(db, session.id)],
        'record_gaps': [{'flow': j.flow, 'agent_id': j.agent_id, 'input_id': j.input_id, 'status': j.status}
                        for j in db.scalars(select(Job).where(Job.session_id == session.id,
                            Job.status.in_(['failed', 'pending', 'running'])))],
        'events': [{'key': e.key, 'due': e.due, 'record_id': e.record_id,
                    'stored_at': e.stored_at, 'received_at': e.received_at}
                   for e in db.scalars(select(Event).where(Event.session_id == session.id))]}
    # Pending AI work cannot mutate or enter the immutable report snapshot.
    for job in db.scalars(select(Job).where(Job.session_id == session.id, Job.flow != 'reports',
                                           Job.status.in_(['pending', 'running']))):
        job.status = 'cancelled'
    enqueue(db, session, 'reports', 'snapshot:2')


def tick_session(db, session, now=None):
    if session.state != 'active':
        return
    current = elapsed(session, now)
    definition = session_scenario(session)
    for spec in definition['events']:
        key, offset, actor, text = (spec[k] for k in ['key', 'offset', 'actor', 'text'])
        event = db.scalar(select(Event).where(Event.session_id == session.id, Event.key == key))
        if current < offset or event.record_id:
            continue
        r = journal(db, session, 'message', actor,
                    {'flow': 'collaboration', 'channel': 'team', 'text': text, 'event_key': key, 'requirement': key == 'change'},
                    'event:' + key, now)
        event.record_id, event.stored_at = r.id, r.at
        if spec.get('review_body'):
            journal(db, session, 'review_draft', 'colleague',
                {'body': spec['review_body'],
                 'source_id': r.id, 'version': 1, 'part_number': spec['review_part']}, 'review:draft', now)
        if spec.get('release_slug') and session.contract:
            slug = spec['release_slug']
            trigger_work(db, session, 'documents:'+slug+':v1', {'document': slug}, now)
    for task in tasks(db, session.id) if session.contract else []:
        if task.owner in ['colleague', 'shared'] and task.status == 'waiting' and (
                task.due_elapsed is not None and current >= task.due_elapsed):
            trigger_work(db, session, 'promise:'+task.id+':'+str(task.version), serialize_task(task), now)
    if current >= 7200:
        close_session(db, session, now)
