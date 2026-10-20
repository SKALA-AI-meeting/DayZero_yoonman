import asyncio
import contextlib
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dayzero.llm.schemas import ModelError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from dayzero.backend.db import DB, Company, Session, Record, Part, Event, Job, AgentMemory, Task, uid
from dayzero.backend.domain import (PARTS, EVENTS, BRIEF, digest, elapsed, require_active, journal,
    enqueue, serialize_record, serialize_part, records, parts, close_session, tick_session, AGENTS, ROLE_VERSION, CONTRACT, tasks, serialize_task)
from dayzero.backend.security import issue, decode, passwords, verify
from dayzero.llm.client import bind
from dayzero.shared.config import settings
from dayzero.shared.scenarios import load_scenario, session_scenario, public_scenario, public_contract, available_scenarios

bearer = HTTPBearer()


def admin(auth: HTTPAuthorizationCredentials = Depends(bearer)):
    return decode(auth.credentials, 'admin')


def get_db():
    with DB.begin() as db:
        yield db


def owned(db, sid, claims, lock=False):
    query = select(Session).where(Session.id == sid, Session.company_id == claims['company_id'])
    session = db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if not session:
        raise HTTPException(404, 'not_found')
    return session


def candidate(db, sid, token, lock=False):
    query = select(Session).where(Session.id == sid, Session.token_hash == digest(token))
    session = db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if not session:
        raise HTTPException(404, 'not_found')
    if session.state == 'invited' and session.expires <= time.time():
        raise HTTPException(410, 'invite_expired')
    return session


def tick_all_sessions():
    # DB row locks must never block the ASGI event loop while a route is awaiting I/O.
    with DB.begin() as db:
        for session in db.scalars(select(Session).where(Session.state == 'active').with_for_update(skip_locked=True).execution_options(populate_existing=True)):
            tick_session(db, session)


async def scheduler():
    while True:
        try:
            await asyncio.to_thread(tick_all_sessions)
        except Exception as error:
            print('scheduler_error', type(error).__name__, flush=True)
        await asyncio.sleep(0.5)


async def publisher():
    from dayzero.backend.queue import publish_outbox
    while True:
        try:
            await publish_outbox()
        except Exception as error:
            print('publisher_error', type(error).__name__, flush=True)
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(scheduler()), asyncio.create_task(publisher())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title='Day Zero', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings().web_origin],
                   allow_methods=['GET', 'POST', 'PATCH'], allow_headers=['Authorization', 'Content-Type'])


@app.exception_handler(ModelError)
async def model_error(request, error):
    return JSONResponse(status_code=503, content={'detail': error.code, 'retryable': error.retryable})


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Credentials(Strict):
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default='우리 회사', min_length=1, max_length=100)


@app.get('/health')
def health(db=Depends(get_db, scope='function')):
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'llm_backend': settings().llm_backend}


@app.post('/auth/register')
def register(body: Credentials, db=Depends(get_db, scope='function')):
    if db.scalar(select(Company).where(Company.email == body.email.lower())):
        raise HTTPException(409, 'email_exists')
    c = Company(email=body.email.lower(), password=passwords.hash(body.password), name=body.name)
    db.add(c)
    try:
        db.flush()
        from dayzero.rag.documents import seed_documents
        seed_documents(db, c.id)
    except IntegrityError:
        raise HTTPException(409, 'email_exists') from None
    return {'token': issue(c.id, c.id), 'name': c.name}


@app.post('/auth/login')
def login(body: Credentials, db=Depends(get_db, scope='function')):
    c = db.scalar(select(Company).where(Company.email == body.email.lower()))
    if not c or not verify(body.password, c.password):
        raise HTTPException(401, 'invalid_credentials')
    return {'token': issue(c.id, c.id), 'name': c.name}


class Invitation(Strict):
    scenario_id: str = 'looplab-v1'
    alias: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


@app.post('/admin/invitations')
def invite(body: Invitation, claims=Depends(admin), db=Depends(get_db, scope='function')):
    try:
        definition = load_scenario(body.scenario_id)
    except ValueError:
        raise HTTPException(422, 'invalid_scenario') from None
    from dayzero.rag.documents import seed_documents
    seed_documents(db, claims['company_id'], definition)
    token = secrets.token_urlsafe(32)
    s = Session(company_id=claims['company_id'], alias=body.alias, email=body.email,
                token_hash=digest(token), expires=time.time()+72*3600,
                scenario_version=definition['id'], contract={'scenario_definition': definition})
    db.add(s)
    db.flush()
    return {'id': s.id, 'url': settings().web_origin+'/invite/'+token, 'expires': s.expires}


@app.get('/admin/scenarios')
def scenarios(claims=Depends(admin)):
    return available_scenarios()


@app.get('/admin/sessions')
def session_list(claims=Depends(admin), db=Depends(get_db, scope='function')):
    return [{'id': s.id, 'alias': s.alias, 'email': s.email, 'state': s.state,
             'binding': s.binding, 'report_binding': s.report_binding or s.binding,
             'scenario': public_scenario(session_scenario(s))} for s in db.scalars(select(Session).where(
                 Session.company_id == claims['company_id']))]


@app.get('/admin/sessions/{sid}/report')
def get_report(sid: str, claims=Depends(admin), db=Depends(get_db, scope='function')):
    s = owned(db, sid, claims)
    return {'state': s.state, 'report': s.report}


@app.post('/admin/sessions/{sid}/report/retry')
def retry_report(sid: str, claims=Depends(admin), db=Depends(get_db, scope='function')):
    s = owned(db, sid, claims, True)
    if s.state != 'failed' or not s.snapshot:
        raise HTTPException(409, 'report_not_failed')
    job = db.scalar(select(Job).where(Job.session_id == sid, Job.flow == 'reports'))
    job.status, job.attempts, job.published, job.error = 'pending', 0, False, None
    job.model_calls, job.execution_started = 0, None
    s.state = 'completed'
    return {'state': s.state}


@app.get('/invitations/{token}')
def invitation(token: str, db=Depends(get_db, scope='function')):
    s = db.scalar(select(Session).where(Session.token_hash == digest(token)))
    if not s:
        raise HTTPException(404, 'invalid_invite')
    if s.state == 'invited' and s.expires < time.time():
        raise HTTPException(410, 'invite_expired')
    return {'id': s.id, 'company': db.get(Company, s.company_id).name, 'alias': s.alias,
            'state': s.state, 'consented': s.consented, 'duration_minutes': 120,
            'scenario': public_scenario(session_scenario(s))}


class Consent(Strict):
    accepted: bool


@app.post('/sessions/{sid}/consent')
def consent(sid: str, body: Consent, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    s = candidate(db, sid, auth.credentials, True)
    if s.state != 'invited' or not body.accepted:
        raise HTTPException(409, 'consent_required')
    s.consented = True
    journal(db, s, 'consent', 'candidate', {'accepted': True, 'notice_version': '2026-10-20'}, 'consent')
    return {'consented': True}


@app.post('/sessions/{sid}/start')
def start(sid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    s = candidate(db, sid, auth.credentials, True)
    if s.state == 'active':
        return {'id': s.id}
    if s.state != 'invited' or not s.consented:
        raise HTTPException(409, 'consent_required_or_closed')
    s.binding = bind(settings()).model_dump()
    s.report_binding = bind(settings(), report=True).model_dump()
    definition = session_scenario(s)
    s.contract = {**CONTRACT, 'scenario_definition': definition}
    s.started, s.state = time.time(), 'active'
    for agent_id in AGENTS:
        db.add(AgentMemory(session_id=s.id, agent_id=agent_id, data={}))
    for i, section in enumerate(definition['sections'], 1):
        db.add(Part(session_id=s.id, number=i, title=section['title'], owner=section['owner']))
    for spec in definition['events']:
        db.add(Event(session_id=s.id, key=spec['key'], due=s.started+spec['offset']))
    lead = journal(db, s, 'message', 'leader', {'requirement': True, 'agent_id': 'leader', 'flow': 'collaboration', 'channel': 'team', 'text': definition['brief']}, 'kickoff:leader')
    enqueue(db, s, 'leader', lead.id)
    r = journal(db, s, 'message', 'colleague', {'flow': 'collaboration', 'channel': 'team',
        'text': definition['roles']['colleague']['introduction']}, 'kickoff:colleague')
    enqueue(db, s, 'colleague', r.id)
    return {'id': s.id, 'binding': s.binding, 'report_binding': s.report_binding}


def view(db, s):
    return {'id': s.id, 'alias': s.alias, 'state': s.state, 'started': s.started,
            'server_now': time.time(), 'remaining': 7200-elapsed(s),
            'parts': [serialize_part(p) for p in parts(db, s.id)],
            'tasks': [serialize_task(t) for t in tasks(db, s.id)], 'contract': public_contract(s.contract),
            'scenario': public_scenario(session_scenario(s), elapsed(s)),
            'records': [serialize_record(r) for r in records(db, s.id)],
            'jobs': [{'id': j.id, 'flow': j.flow, 'agent_id': j.agent_id, 'input_id': j.input_id, 'status': j.status, 'error': j.error}
                     for j in db.scalars(select(Job).where(Job.session_id == s.id).order_by(Job.flow, Job.sequence))],
            'confirmed_receipts': [e.record_id for e in db.scalars(select(Event).where(Event.session_id == s.id, Event.received_at.is_not(None)))],
            'division': {'rounds': s.negotiation_rounds, 'agreed': s.division_agreed}}


@app.get('/sessions/{sid}')
def workspace(sid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    s = candidate(db, sid, auth.credentials, True)
    tick_session(db, s)
    return view(db, s)


class Message(Strict):
    flow: Literal['collaboration', 'chat']
    channel: Literal['team', 'dm', 'assistant']
    text: str = Field(min_length=1, max_length=12000)
    operation_id: str = Field(min_length=1, max_length=100)
    source_message_id: str | None = None
    recipients: list[Literal['leader', 'colleague', 'chat']] | None = Field(default=None, min_length=1, max_length=2)
    reply_to: str | None = None


def recipients(db, sid, body):
    allowed = ['chat'] if body.flow == 'chat' else ['colleague'] if body.channel == 'dm' else ['leader', 'colleague']
    target = body.recipients
    if body.reply_to:
        reply = db.get(Record, body.reply_to)
        if not reply or reply.session_id != sid or reply.kind != 'message' or reply.data.get('channel') != body.channel:
            raise HTTPException(422, 'invalid_reply')
        target = target or [('chat' if reply.actor == 'assistant' else reply.actor)]
    if not target:
        target = ['chat'] if body.flow == 'chat' else ['colleague']
        if body.channel == 'team':
            people = session_scenario(db.get(Session, sid))['roles']
            mentions = [a for a, names in [('leader', ['@팀장', '@'+people['leader']['name']]), ('colleague', ['@동료', '@'+people['colleague']['name']])]
                        if any(name in body.text for name in names)]
            target = mentions or target
    if len(set(target)) != len(target) or not set(target) <= set(allowed):
        raise HTTPException(422, 'invalid_recipient')
    return target


@app.post('/sessions/{sid}/messages')
def message(sid: str, body: Message, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    s = candidate(db, sid, auth.credentials, True)
    require_active(s)
    if (body.flow == 'chat') != (body.channel == 'assistant'):
        raise HTTPException(422, 'invalid_channel')
    data = body.model_dump(exclude={'operation_id'})
    data['recipients'] = recipients(db, sid, body)
    operation_id = 'user:message:' + body.operation_id
    old = db.scalar(select(Record).where(Record.session_id == sid, Record.operation_id == operation_id))
    if old:
        if old.data != data:
            raise HTTPException(409, 'idempotency_conflict')
        return serialize_record(old)
    if body.source_message_id:
        source = db.get(Record, body.source_message_id)
        if not source or source.session_id != sid or source.actor != 'assistant':
            raise HTTPException(422, 'invalid_source')
    r = journal(db, s, 'message', 'candidate', data, operation_id)
    for agent_id in data['recipients']:
        enqueue(db, s, agent_id, r.id)
    return serialize_record(r)


class PartChange(Strict):
    version: int
    operation_id: str = Field(min_length=1, max_length=100)
    body: str | None = Field(default=None, max_length=20000)
    status: Literal['todo', 'doing', 'done'] | None = None
    source_message_id: str | None = None


def change_part(db, s, part_id, change, actor, job_id=None):
    require_active(s)
    operation_id = ('user:part:' if actor == 'candidate' else 'tool:part:') + change.operation_id
    old = db.scalar(select(Record).where(Record.session_id == s.id, Record.operation_id == operation_id))
    if old:
        if (old.data.get('after', {}).get('id') != part_id or old.data.get('requested_change') != change.model_dump(exclude={'operation_id'})):
            raise HTTPException(409, 'idempotency_conflict')
        return old.data['after']
    p = db.scalar(select(Part).where(Part.session_id == s.id, Part.id == part_id))
    if not p:
        raise HTTPException(404, 'not_found')
    if p.version != change.version:
        raise HTTPException(409, 'version_conflict')
    if change.status and actor not in [p.owner, 'colleague'] and p.owner != 'shared':
        raise HTTPException(403, 'task_not_owned')
    if actor == 'colleague' and p.owner == 'candidate':
        raise HTTPException(403, 'part_not_owned')
    if change.source_message_id:
        src = db.get(Record, change.source_message_id)
        if actor != 'candidate' or not src or src.session_id != s.id or src.actor != 'assistant':
            raise HTTPException(422, 'invalid_source')
    before = serialize_part(p)
    if change.body is not None:
        p.body = change.body
    if change.status:
        p.status = change.status
    p.version += 1
    after = serialize_part(p)
    journal(db, s, 'part', actor, {'before': before, 'after': after,
        'source_message_id': change.source_message_id, 'job_id': job_id,
        'requested_change': change.model_dump(exclude={'operation_id'})}, operation_id)
    return after


@app.patch('/sessions/{sid}/parts/{part_id}')
def edit_part(sid: str, part_id: str, body: PartChange, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    return change_part(db, candidate(db, sid, auth.credentials, True), part_id, body, 'candidate')


from dayzero.contracts.work import TaskCreate, TaskChange


class NewTask(TaskCreate):
    operation_id: str = Field(min_length=1, max_length=100)


class EditTask(TaskChange):
    operation_id: str = Field(min_length=1, max_length=100)


@app.post('/sessions/{sid}/tasks')
def add_task(sid: str, body: NewTask, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    from dayzero.backend.tasks import create_task
    s = candidate(db, sid, auth.credentials, True)
    return create_task(db, s, TaskCreate(**body.model_dump(exclude={'operation_id'})),
                       'candidate', 'user:task:'+body.operation_id)


@app.patch('/sessions/{sid}/tasks/{task_id}')
def edit_task(sid: str, task_id: str, body: EditTask, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    from dayzero.backend.tasks import update_task
    s = candidate(db, sid, auth.credentials, True)
    return update_task(db, s, task_id, TaskChange(**body.model_dump(exclude={'operation_id'}, exclude_unset=True)),
                       'candidate', 'user:task:'+body.operation_id)


@app.post('/sessions/{sid}/submit')
def submit(sid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    s = candidate(db, sid, auth.credentials, True)
    close_session(db, s)
    return {'state': s.state}


class Receipt(Strict):
    ids: list[str] = Field(max_length=100)


@app.post('/sessions/{sid}/receipts')
def receipt(sid: str, body: Receipt, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    from sqlalchemy import update
    candidate(db, sid, auth.credentials)
    result = db.execute(update(Event).where(Event.session_id == sid, Event.record_id.in_(body.ids),
        Event.received_at.is_(None)).values(received_at=time.time()))
    return {'received': result.rowcount}


@app.post('/sessions/{sid}/reads')
def mark_read(sid: str, body: Receipt, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    s = candidate(db, sid, auth.credentials, True)
    require_active(s)
    for rid in set(body.ids):
        r = db.get(Record, rid)
        if not r or r.session_id != sid or r.kind != 'message' or r.data.get('flow') != 'collaboration':
            raise HTTPException(422, 'invalid_read_source')
        journal(db, s, 'read', 'candidate', {'message_id': rid, 'channel': r.data['channel']}, 'read:'+rid)
    return {'ok': True}


@app.get('/sessions/{sid}/debrief')
def debrief(sid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    s = candidate(db, sid, auth.credentials)
    if s.state in ['invited', 'active']:
        raise HTTPException(409, 'session_not_finished')
    return {'state': s.state, 'scenario': public_scenario(session_scenario(s), elapsed(s)),
            'situations': [e['debrief'] for e in session_scenario(s)['events'] if e.get('debrief')]}


@app.get('/sessions/{sid}/documents')
def documents(sid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    from dayzero.rag.documents import visible_documents
    s = candidate(db, sid, auth.credentials)
    return visible_documents(db, s.company_id, elapsed(s), session_scenario(s))


@app.get('/sessions/{sid}/documents/{doc_id}')
def document(sid: str, doc_id: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    from dayzero.rag.documents import get_document
    s = candidate(db, sid, auth.credentials)
    return get_document(db, s.company_id, elapsed(s), doc_id, session_scenario(s))


def websocket_snapshot(sid, token):
    with DB.begin() as db:
        return view(db, candidate(db, sid, token))


def acknowledge_events(sid, received):
    from sqlalchemy import update
    with DB.begin() as db:
        db.execute(update(Event).where(Event.session_id == sid, Event.record_id.in_(received),
            Event.received_at.is_(None)).values(received_at=time.time()))


@app.websocket('/sessions/{sid}/ws')
async def realtime(websocket: WebSocket, sid: str):
    await websocket.accept()
    try:
        token = await asyncio.wait_for(websocket.receive_text(), 5)
        sent = set()
        while True:
            snapshot = await asyncio.to_thread(websocket_snapshot, sid, token)
            await websocket.send_json(snapshot)
            sent.update(r['id'] for r in snapshot['records'])
            try:
                ack = await asyncio.wait_for(websocket.receive_json(), 1)
                received = sent.intersection(ack.get('received', []))
                if received:
                    await asyncio.to_thread(acknowledge_events, sid, list(received))
            except TimeoutError:
                pass
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, HTTPException, TimeoutError):
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1008)


# Internal routes use service authentication for claiming and short-lived job tokens for data.
def service(x_internal_secret: str = Header(default='')):
    if not secrets.compare_digest(x_internal_secret, settings().internal_secret.get_secret_value()):
        raise HTTPException(401, 'invalid_service')


def job_scope(db, jid, token, allow_closed=False):
    claims = decode(token, 'job')
    initial = db.get(Job, jid)
    if not initial or claims['sub'] != jid:
        raise HTTPException(403, 'invalid_job_scope')
    s = owned(db, initial.session_id, claims, True)
    job = db.scalar(select(Job).where(Job.id == jid).with_for_update().execution_options(populate_existing=True))
    if claims.get('agent_id') != job.agent_id or claims.get('role_version') != job.role_version:
        raise HTTPException(403, 'invalid_agent_scope')
    if claims.get('lease_id') != job.lease_id:
        raise HTTPException(403, 'invalid_job_scope')
    if job.status != 'running' or job.lease_until < time.time():
        raise HTTPException(409, 'stale_lease')
    if not allow_closed:
        require_active(s)
    return job, s


@app.post('/internal/jobs/{jid}/claim', dependencies=[Depends(service)])
def claim(jid: str, db=Depends(get_db, scope='function')):
    initial = db.get(Job, jid)
    if not initial:
        raise HTTPException(404, 'job_not_found')
    # Lock order is always session -> job, including result and tool APIs.
    s = db.scalar(select(Session).where(Session.id == initial.session_id).with_for_update().execution_options(populate_existing=True))
    job = db.scalar(select(Job).where(Job.id == jid).with_for_update().execution_options(populate_existing=True))
    if job.status in ['done', 'failed', 'cancelled']:
        return {'terminal': True, 'status': job.status}
    if job.flow != 'reports' and s.state != 'active':
        job.status = 'cancelled'
        return {'terminal': True, 'status': job.status}
    earlier = db.scalar(select(Job.id).where(Job.session_id == s.id, Job.agent_id == job.agent_id,
        Job.sequence < job.sequence, Job.status.in_(['pending', 'running'])).limit(1))
    if earlier or (job.status == 'running' and job.lease_until > time.time()):
        raise HTTPException(409, 'busy')
    job.status, job.lease_until, job.lease_id = 'running', time.time()+45, uid()
    if job.execution_started is None:
        job.execution_started = time.time()
    if time.time() > job.execution_started + 450:
        job.status, job.error = 'failed', 'job_time_budget_exhausted'
        if job.flow == 'reports':
            s.state = 'failed'
        return {'terminal': True, 'status': job.status}
    job.attempts += 1
    return {'terminal': False, 'id': job.id, 'session_id': s.id, 'flow': job.flow, 'agent_id': job.agent_id, 'role_version': job.role_version,
            'input_id': job.input_id, 'binding': (s.report_binding or s.binding) if job.flow == 'reports' else s.binding, 'execution_deadline': job.execution_started+450,
            'resume': job.error != 'event_context_changed',
            'token': issue(job.id, s.company_id, 'job', lease_id=job.lease_id, agent_id=job.agent_id, role_version=job.role_version)}


@app.post('/internal/jobs/{jid}/heartbeat')
def heartbeat(jid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    job, _ = job_scope(db, jid, auth.credentials, True)
    job.lease_until = min(time.time()+45, job.execution_started+450)
    return {'ok': True}


@app.post('/internal/jobs/{jid}/model-attempt')
def reserve_model_attempt(jid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    job, _ = job_scope(db, jid, auth.credentials, job_is_report(db, jid))
    limit = 3 if job.flow == 'reports' else 6
    if job.model_calls >= limit:
        raise HTTPException(429, 'model_budget_exhausted')
    job.model_calls += 1
    return {'attempt': job.model_calls, 'limit': limit}


class ToolReservation(Strict):
    operation_id: str = Field(min_length=1, max_length=200)
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')


@app.post('/internal/jobs/{jid}/tool-attempt')
def reserve_tool(jid: str, body: ToolReservation, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    job, _ = job_scope(db, jid, auth.credentials)
    if job.agent_id not in AGENTS:
        raise HTTPException(403, 'tool_not_allowed')
    old = job.tool_operations.get(body.operation_id)
    if old and old != body.digest:
        raise HTTPException(409, 'idempotency_conflict')
    if not old:
        if len(job.tool_operations) >= 3:
            raise HTTPException(429, 'tool_budget_exhausted')
        job.tool_operations = {**job.tool_operations, body.operation_id: body.digest}
    return {'calls': len(job.tool_operations), 'limit': 3}


def visible_to(record, agent_id):
    if record.kind != 'message':
        return False
    data = record.data
    if agent_id == 'chat':
        return data.get('flow') == 'chat'
    return data.get('flow') == 'collaboration' and (data.get('channel') == 'team' or
        (agent_id == 'colleague' and data.get('channel') == 'dm'))


@app.get('/internal/jobs/{jid}/context')
def context(jid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    job, s = job_scope(db, jid, auth.credentials, job_is_report(db, jid))
    if job.flow == 'reports':
        return s.snapshot
    request = db.get(Record, job.input_id)
    channel = request.data.get('channel', 'team')
    if job.agent_id not in AGENTS or job.role_version != ROLE_VERSION:
        raise HTTPException(409, 'legacy_agent_unsupported')
    all_records = records(db, s.id)
    history = [serialize_record(r) for r in all_records if visible_to(r, job.agent_id)
        and (r.actor != 'candidate' or r.at <= request.at)]
    memory = db.get(AgentMemory, (s.id, job.agent_id))
    public_events = list(db.scalars(select(Event).where(Event.session_id == s.id, Event.record_id.is_not(None))))
    definition = session_scenario(s)
    task_brief = definition['brief']
    changes = [r for r in all_records if r.kind=='message' and r.data.get('requirement')]
    if changes:
        task_brief = changes[-1].data['text']
    # No future events, other channels, other sessions or report criteria.
    return {'id': s.id, 'flow': job.flow, 'agent_id': job.agent_id, 'channel': channel, 'binding': s.binding,
            'role_version': job.role_version, 'memory': {'version': memory.version, 'data': memory.data},
            'tasks': [serialize_task(t) for t in tasks(db, s.id)],
            'elapsed': elapsed(s), 'task_brief': task_brief,
            'scenario': public_scenario(definition, elapsed(s)), 'event_version': len(public_events),
            'tool_calls': len(job.tool_operations),
            'mutation_ids': [r.id for r in all_records if r.kind in ['part', 'task', 'division']
                and r.actor == job.agent_id and r.data.get('job_id') == job.id],
            'parts': [serialize_part(p) for p in parts(db, s.id)],
            'history': history[-30:], 'input': serialize_record(request),
            'division': {'rounds': s.negotiation_rounds, 'agreed': s.division_agreed}}


def job_is_report(db, jid):
    job = db.get(Job, jid)
    return bool(job and job.flow == 'reports')


class ToolInput(Strict):
    name: str
    arguments: dict
    operation_id: str
    context_event_version: int | None = None


@app.post('/internal/jobs/{jid}/business')
def business(jid: str, body: ToolInput, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    from dayzero.agents.harness import validate_tool
    job, s = job_scope(db, jid, auth.credentials)
    args = validate_tool(job.agent_id, body.name, body.arguments,
                         part_numbers=[p.number for p in parts(db, s.id)])
    if body.name == 'get_workspace':
        return {'parts': [serialize_part(p) for p in parts(db, s.id)], 'tasks': [serialize_task(t) for t in tasks(db, s.id)], 'elapsed': elapsed(s)}
    if body.name == 'get_history':
        return {'records': [serialize_record(r) for r in records(db, s.id) if r.kind in ['part', 'task', 'division']][-30:]}
    if body.name in ['update_part', 'negotiate', 'create_task', 'update_task', 'complete_task', 'remember']:
        current = len(list(db.scalars(select(Event.id).where(Event.session_id == s.id, Event.record_id.is_not(None)))))
        if body.context_event_version is None:
            raise HTTPException(422, 'model_context_missing')
        if body.context_event_version != current:
            raise HTTPException(409, 'event_context_changed')
    if body.name == 'update_part':
        part = change_part(db, s, args['part_id'], PartChange(**args['change'],
                           operation_id=body.operation_id), 'colleague', job.id)
        proof = db.scalar(select(Record).where(Record.session_id == s.id, Record.operation_id == 'tool:part:'+body.operation_id))
        return {'part': part, 'mutation_ids': [proof.id]}
    if body.name in ['create_task', 'update_task', 'complete_task']:
        from dayzero.backend.tasks import create_task, update_task, complete_task
        from dayzero.contracts.work import TaskCreate, TaskChange, CompleteTask
        op = 'tool:task:'+body.operation_id
        if body.name == 'create_task':
            result = create_task(db, s, TaskCreate(**args), 'colleague', op, job.id)
        elif body.name == 'update_task':
            result = update_task(db, s, args['task_id'], TaskChange(**args['change']), 'colleague', op, job.id)
        else:
            result = complete_task(db, s, CompleteTask(**args), op, job.id)
        proof = db.scalar(select(Record).where(Record.session_id == s.id, Record.operation_id == op))
        return {'task': result, 'mutation_ids': [proof.id] + ([proof.data['part_record_id']] if 'part_record_id' in proof.data else [])}
    if body.name == 'remember':
        source = db.get(Record, args['source_id'])
        if not source or source.session_id != s.id or not visible_to(source, job.agent_id):
            raise HTTPException(403, 'memory_source_not_visible')
        old = db.scalar(select(Record).where(Record.session_id == s.id, Record.operation_id == 'memory:'+body.operation_id))
        if old:
            if old.data.get('note') != args:
                raise HTTPException(409, 'idempotency_conflict')
            return {'record_id': old.id}
        memory = db.get(AgentMemory, (s.id, job.agent_id))
        memory.data = {**memory.data, 'notes': (memory.data.get('notes', [])+[args])[-30:]}
        memory.version += 1
        r = journal(db, s, 'memory', job.agent_id, {'note': args, 'version': memory.version}, 'memory:'+body.operation_id)
        return {'record_id': r.id, 'version': memory.version}
    if body.name == 'negotiate':
        previous = db.scalar(select(Record).where(Record.session_id == s.id, Record.operation_id == body.operation_id))
        if previous:
            if previous.data.get('request', args) != args:
                raise HTTPException(409, 'idempotency_conflict')
            assigned = db.scalars(select(Record.id).where(Record.session_id == s.id,
                Record.kind == 'task', Record.data['request']['division'].astext == body.operation_id).order_by(Record.at)).all()
            return {'rounds': previous.data['rounds'], 'agreed': previous.data['agreed'], 'mutation_ids': [previous.id]+list(assigned)}
        if session_scenario(s)['workflow'] == 'conversation':
            from dayzero.backend.tasks import source_record
            source_record(db, s, args.get('source_id'), 'colleague')
            if not args['accepted']:
                raise HTTPException(409, 'division_confirmation_required')
            s.negotiation_rounds += 1
            s.division_agreed = True
        elif s.division_agreed or s.negotiation_rounds >= 3:
            from dayzero.backend.tasks import source_record
            source_record(db, s, args.get('source_id'), 'colleague')
            if not args['accepted']:
                raise HTTPException(409, 'division_confirmation_required')
        else:
            s.negotiation_rounds += 1
            s.division_agreed = args['accepted'] or s.negotiation_rounds == 3
        before = [serialize_part(p) for p in parts(db, s.id)]
        for p in parts(db, s.id):
            p.owner = args['owners'][str(p.number)]
            p.version += 1
        proof = journal(db, s, 'division', 'colleague', {'before': before,
                'after': [serialize_part(p) for p in parts(db, s.id)],
                'rounds': s.negotiation_rounds, 'agreed': s.division_agreed, 'source_id': args.get('source_id'), 'job_id': job.id, 'request': args}, body.operation_id)
        assigned = []
        if s.division_agreed:
            from dayzero.backend.tasks import assign_division
            assigned = assign_division(db, s, args.get('source_id'), job.id, body.operation_id)
        return {'rounds': s.negotiation_rounds, 'agreed': s.division_agreed, 'mutation_ids': [proof.id]+assigned}
    raise HTTPException(403, 'tool_not_allowed')


@app.get('/internal/jobs/{jid}/documents')
def internal_documents(jid: str, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    from dayzero.rag.documents import visible_documents
    _, s = job_scope(db, jid, auth.credentials)
    return visible_documents(db, s.company_id, elapsed(s), session_scenario(s))


class Result(Strict):
    text: str = ''
    sources: list[dict] = Field(default_factory=list)
    report: dict | None = None
    usage: dict | None = None
    error: str | None = None
    retryable: bool = False
    context_event_version: int | None = None
    agent_id: Literal['leader', 'colleague', 'chat'] | None = None
    mutation_ids: list[str] = Field(default_factory=list, max_length=20)


@app.post('/internal/jobs/{jid}/result')
def save_result(jid: str, body: Result, auth=Depends(bearer), db=Depends(get_db, scope='function')):
    claims = decode(auth.credentials, 'job')
    initial = db.get(Job, jid)
    if not initial or claims['sub'] != jid:
        raise HTTPException(403, 'invalid_job_scope')
    s = owned(db, initial.session_id, claims, True)
    job = db.scalar(select(Job).where(Job.id == jid).with_for_update().execution_options(populate_existing=True))
    if claims.get('agent_id') != job.agent_id or claims.get('role_version') != job.role_version:
        raise HTTPException(403, 'invalid_agent_scope')
    if claims.get('lease_id') != job.lease_id:
        raise HTTPException(409, 'stale_lease')
    if job.status in ['done', 'cancelled']:
        return {'status': job.status}
    if job.status != 'running' or job.lease_until < time.time():
        raise HTTPException(409, 'stale_lease')
    if body.error:
        job.error = body.error
        job.status = 'pending' if body.retryable and job.attempts < 3 else 'failed'
        job.published = False
        if job.status == 'failed' and job.flow == 'reports':
            s.state = 'failed'
        return {'status': job.status}
    if job.flow == 'reports':
        from dayzero.reporting.build import validate_report
        validate_report(body.report, s.snapshot)
        s.report, s.state = body.report, 'report_ready'
    else:
        require_active(s)
        current_event_version = len(list(db.scalars(select(Event.id).where(Event.session_id == s.id, Event.record_id.is_not(None)))))
        if body.context_event_version is None:
            raise HTTPException(422, 'model_context_missing')
        if body.context_event_version != current_event_version:
            job.status, job.published, job.error = 'pending', False, 'event_context_changed'
            return {'status': 'pending', 'reason': 'event_context_changed'}
        from dayzero.agents.harness import guard_text
        guard_text(body.text)
        from dayzero.rag.documents import visible_documents
        allowed = {d['id']: d for d in visible_documents(db, s.company_id, elapsed(s), session_scenario(s))}
        for source in body.sources:
            d = allowed.get(source.get('id'))
            if not d or source.get('version') != d['version']:
                raise HTTPException(422, 'invalid_source')
        source_input = db.get(Record, job.input_id)
        if body.agent_id and body.agent_id != job.agent_id:
            raise HTTPException(403, 'sender_mismatch')
        actor = 'assistant' if job.agent_id == 'chat' else job.agent_id
        for rid in body.mutation_ids:
            proof = db.get(Record, rid)
            if not proof or proof.session_id != s.id or proof.actor != actor or proof.kind not in ['task', 'part', 'division'] or proof.data.get('job_id') != job.id:
                raise HTTPException(422, 'invalid_mutation_evidence')
        memory = db.get(AgentMemory, (s.id, job.agent_id))
        memory.data = {**memory.data, 'last_input_id': job.input_id, 'last_job_id': job.id,
                       'last_mutation_ids': body.mutation_ids}
        memory.version += 1
        journal(db, s, 'message', actor,
            {'flow': job.flow, 'channel': source_input.data.get('channel', 'team'),
             'text': body.text, 'sources': body.sources, 'reply_to': job.input_id, 'job_id': job.id,
             'binding': s.binding, 'agent_id': job.agent_id, 'role_version': job.role_version,
             'mutation_ids': body.mutation_ids, 'usage': body.usage, 'model_attempts': job.model_calls}, 'job:'+job.id)
    job.status, job.result = 'done', {'binding': (s.report_binding or s.binding) if job.flow == 'reports' else s.binding, 'usage': body.usage, 'model_attempts': job.model_calls, 'queue_attempts': job.attempts,
        'processing_seconds': time.time()-job.execution_started, 'trace_id': job.id}
    return {'status': 'done'}
