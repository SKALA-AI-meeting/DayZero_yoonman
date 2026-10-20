from fastapi import HTTPException
from sqlalchemy import select
from dayzero.backend.db import Task, Part, Record
from dayzero.backend.domain import require_active, journal, serialize_task, serialize_part, trigger_work, elapsed


def source_record(db, session, source_id, actor):
    source = db.get(Record, source_id) if source_id else None
    if not source or source.session_id != session.id or source.kind != 'message':
        raise HTTPException(422, 'invalid_task_source')
    if actor == 'colleague' and (source.actor != 'candidate' or
            source.data.get('flow') != 'collaboration' or
            'colleague' not in source.data.get('recipients', ['colleague'])):
        raise HTTPException(403, 'agreement_required')
    return source


def operation(db, session, operation_id, request):
    old = db.scalar(select(Record).where(Record.session_id == session.id, Record.operation_id == operation_id))
    if old and old.data.get('request') != request:
        raise HTTPException(409, 'idempotency_conflict')
    return old


def create_task(db, session, value, actor, operation_id, job_id=None):
    require_active(session)
    request = value.model_dump()
    if request['status']=='todo':request.pop('status')
    if request['due_elapsed'] is None:request.pop('due_elapsed')
    if request['delay_seconds'] is None:request.pop('delay_seconds')
    old = operation(db, session, operation_id, request)
    if old:
        return old.data['after']
    if actor == 'colleague' or value.source_id:
        source_record(db, session, value.source_id, actor)
    if value.status=='waiting' and (value.due_elapsed is None or value.due_elapsed <= elapsed(session)):
        if value.delay_seconds is None:
            raise HTTPException(422, 'future_promise_required')
    values = {k:v for k,v in request.items() if k!='delay_seconds'}
    if value.delay_seconds is not None:
        if value.due_elapsed is not None:
            raise HTTPException(422, 'ambiguous_promise_time')
        values.update(status='waiting',due_elapsed=elapsed(session)+value.delay_seconds)
        if values['due_elapsed'] > 7200:
            raise HTTPException(422, 'promise_after_deadline')
    if value.part_id:
        part = db.get(Part, value.part_id)
        if not part or part.session_id != session.id:
            raise HTTPException(404, 'part_not_found')
    task = Task(session_id=session.id, **values)
    db.add(task)
    db.flush()
    after = serialize_task(task)
    journal(db, session, 'task', actor, {'before': None, 'after': after,
        'request': request, 'job_id': job_id}, operation_id)
    if actor == 'candidate' and task.owner in ['colleague', 'shared'] and task.status=='todo':
        trigger_work(db, session, 'assignment:'+task.id+':1', after)
    return after


def update_task(db, session, task_id, value, actor, operation_id, job_id=None):
    require_active(session)
    request = {'task_id': task_id, **value.model_dump()}
    old = operation(db, session, operation_id, request)
    if old:
        return old.data['after']
    task = db.get(Task, task_id)
    if not task or task.session_id != session.id:
        raise HTTPException(404, 'task_not_found')
    if task.version != value.version:
        raise HTTPException(409, 'version_conflict')
    if actor == 'colleague':
        if task.owner == 'candidate':
            raise HTTPException(403, 'task_not_owned')
        if value.status == 'done' and task.part_id:
            raise HTTPException(409, 'complete_task_required')
        if value.owner and value.owner != task.owner:
            source_record(db, session, value.source_id, actor)
    elif value.source_id:
        source_record(db, session, value.source_id, actor)
    due = value.due_elapsed if value.due_elapsed is not None else task.due_elapsed
    new_status = value.status or task.status
    if new_status == 'waiting' and (due is None or due <= elapsed(session)):
        raise HTTPException(422, 'future_promise_required')
    before = serialize_task(task)
    for key, v in value.model_dump(exclude_unset=True, exclude={'version'}).items():
        if v is not None:
            setattr(task, key, v)
    task.version += 1
    after = serialize_task(task)
    journal(db, session, 'task', actor, {'before': before, 'after': after,
        'request': request, 'job_id': job_id}, operation_id)
    if actor == 'candidate' and task.owner in ['colleague', 'shared'] and task.status in ['todo', 'doing']:
        trigger_work(db, session, 'assignment:'+task.id+':'+str(task.version), after)
    return after


def complete_task(db, session, value, operation_id, job_id):
    require_active(session)
    request = value.model_dump()
    old = operation(db, session, operation_id, request)
    if old:
        return old.data['result']
    task = db.get(Task, value.task_id)
    if not task or task.session_id != session.id or task.owner not in ['colleague', 'shared']:
        raise HTTPException(403, 'task_not_owned')
    if task.status in ['done', 'cancelled']:
        raise HTTPException(409, 'task_closed')
    if task.status == 'waiting' and task.due_elapsed > elapsed(session):
        raise HTTPException(409, 'promise_not_due')
    part = db.get(Part, task.part_id)
    if not part or part.session_id != session.id or part.owner == 'candidate':
        raise HTTPException(403, 'part_not_owned')
    if task.version != value.version or part.version != value.part_version:
        raise HTTPException(409, 'version_conflict')
    before_part, before = serialize_part(part), serialize_task(task)
    part.body, part.version = value.body, part.version+1
    task.status, task.note, task.version = 'done', value.note, task.version+1
    change = journal(db, session, 'part', 'colleague', {'before': before_part,
        'after': serialize_part(part), 'task_id': task.id, 'job_id': job_id}, operation_id+':part')
    after = serialize_task(task)
    result = {'after': after, 'part': serialize_part(part), 'part_record_id': change.id}
    journal(db, session, 'task', 'colleague', {'before': before, 'after': after, 'result': result,
        'request': request, 'job_id': job_id, 'part_record_id': change.id}, operation_id)
    return result
