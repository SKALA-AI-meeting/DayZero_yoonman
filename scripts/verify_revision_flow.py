"""Short real-model verification in a disposable database and RabbitMQ vhost.

Only a new fictional Looplab session reaches the models. Existing services,
sessions and queues stay untouched. This is not a real 120-minute run.
"""
import asyncio
import json
import os
import re
import socket
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
import psycopg
from psycopg import sql
from dotenv import dotenv_values

from dayzero.shared.config import settings


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def main():
    cfg = settings()
    run_id = uuid.uuid4().hex
    database = 'dayzero_revision_' + run_id
    source = cfg.database_url.replace('postgresql+psycopg://', 'postgresql://')
    isolated = urlunsplit(urlsplit(source)._replace(path='/' + database))
    rabbit = urlsplit(cfg.rabbitmq_url)
    isolated_rabbit = urlunsplit(rabbit._replace(path='/' + database))
    manager_url = f'http://{rabbit.hostname}:15673'
    manager_auth = (unquote(rabbit.username), unquote(rabbit.password))
    backend_port, business_port, rag_port = free_port(), free_port(), free_port()
    backend_url = f'http://127.0.0.1:{backend_port}'
    env = {
        **os.environ,
        'DATABASE_URL': isolated.replace('postgresql://', 'postgresql+psycopg://'),
        'CHECKPOINT_DATABASE_URL': urlunsplit(urlsplit(cfg.checkpoint_database_url)._replace(path='/' + database)),
        'RAG_DATABASE_URL': urlunsplit(urlsplit(cfg.rag_database_url)._replace(path='/' + database)),
        'RABBITMQ_URL': isolated_rabbit,
        'BACKEND_URL': backend_url,
        'BUSINESS_MCP_URL': f'http://127.0.0.1:{business_port}/mcp',
        'RAG_MCP_URL': f'http://127.0.0.1:{rag_port}/mcp',
        'MCP_HOST': '127.0.0.1',
    }
    hf_home = dotenv_values('.env').get('HF_HOME')
    if hf_home:
        env['HF_HOME'] = hf_home
    result = {
        'synthetic_fixture': True,
        'isolated_database_and_vhost': True,
        'actual_backend': True,
        'actual_model': True,
        'compressed_clock': True,
        'full_120_minutes': False,
        'completed': False,
        'steps': [],
    }
    started = time.monotonic()
    evidence = Path('docs/evidence/codex-revision-verification.json')
    children = []
    log_files = []
    created_db = created_vhost = False
    sid = None
    stage = 'setup'
    secrets = [cfg.jwt_secret.get_secret_value(), cfg.internal_secret.get_secret_value(),
        cfg.openai_api_key.get_secret_value()]
    db_admin = psycopg.connect(source, autocommit=True)

    def checkpoint(label, **details):
        result['steps'].append({'step': label, 'seconds': round(time.monotonic()-started, 2), **details})
        evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(label, flush=True)

    async def launch(name, args, logs, api_key=False):
        log = open(Path(logs)/f'{name}.log', 'w+')
        log_files.append(log)
        command = [sys.executable, 'scripts/with_zsh_api_key.py', *args] if api_key else args
        child = await asyncio.create_subprocess_exec(*command, env=env, stdout=log, stderr=log)
        children.append((name, child))
        return child

    async def healthy():
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(100):
                if any(child.returncode is not None for _, child in children):
                    raise RuntimeError('isolated_service_exited')
                try:
                    health = await client.get(backend_url + '/health')
                    business = await client.get(env['BUSINESS_MCP_URL'])
                    rag = await client.get(env['RAG_MCP_URL'])
                    if health.status_code == 200 and business.status_code == rag.status_code == 406:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(.2)
        raise TimeoutError('isolated_startup_timeout')

    try:
        with tempfile.TemporaryDirectory(prefix='dayzero-revision-') as logs:
            db_admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            created_db = True
            migration = await launch('migration', [
                'services/dayzero/.venv/bin/alembic', '-c', 'services/dayzero/alembic.ini', 'upgrade', 'head',
            ], logs)
            assert await migration.wait() == 0, 'isolated_migration_failed'
            children.remove(('migration', migration))
            with psycopg.connect(isolated) as db:
                db.execute('CREATE SCHEMA checkpoints AUTHORIZATION graph')
                db.execute('GRANT USAGE ON SCHEMA public TO rag')
            async with httpx.AsyncClient(base_url=manager_url, auth=manager_auth, timeout=5) as manager:
                response = await manager.put('/api/vhosts/' + database)
                response.raise_for_status()
                created_vhost = True
                response = await manager.put('/api/permissions/' + database + '/' + quote(manager_auth[0], safe=''),
                    json={'configure': '.*', 'write': '.*', 'read': '.*'})
                response.raise_for_status()
            await launch('backend', [sys.executable, '-m', 'uvicorn', 'dayzero.backend.main:app',
                '--host', '127.0.0.1', '--port', str(backend_port), '--no-access-log'], logs)
            for service in ['business', 'rag']:
                await launch(service, [sys.executable, '-m', 'dayzero.mcp_servers.' + service], logs)
            await healthy()
            checkpoint('isolated_services_ready')

            async with httpx.AsyncClient(base_url=backend_url, timeout=20) as client:
                async def call(path, method='GET', payload=None, auth=None):
                    response = await client.request(method, path, json=payload, headers=auth)
                    if response.is_error:
                        detail = response.json().get('detail') if response.headers.get('content-type', '').startswith('application/json') else ''
                        raise AssertionError(f'HTTP_{response.status_code}_{detail}')
                    return response.json()

                registration = await call('/auth/register', 'POST', {
                    'email': 'revision-' + run_id + '@example.com',
                    'password': uuid.uuid4().hex,
                    'name': 'Codex 수정 검증 합성 기업',
                })
                admin = {'Authorization': 'Bearer ' + registration['token']}
                secrets.append(registration['token'])
                invitation = await call('/admin/invitations', 'POST', {
                    'alias': '자동 분업 실제 모델 합성 검증', 'email': 'synthetic@example.com',
                    'scenario_id': 'looplab-v1',
                }, admin)
                sid = invitation['id']
                candidate = {'Authorization': 'Bearer ' + urlsplit(invitation['url']).path.rsplit('/', 1)[1]}
                secrets.append(candidate['Authorization'].removeprefix('Bearer '))
                with psycopg.connect(isolated) as db:
                    docs = db.execute('SELECT id, slug, body FROM company_documents').fetchall()
                    copied = 0
                    for doc_id, slug, body in docs:
                        embedding = db_admin.execute(
                            'SELECT embedding::text FROM company_documents WHERE slug=%s AND body=%s '
                            'AND embedding IS NOT NULL LIMIT 1', (slug, body)).fetchone()
                        assert embedding, 'fictional_document_embedding_missing'
                        db.execute('UPDATE company_documents SET embedding=%s::vector WHERE id=%s', (embedding[0], doc_id))
                        copied += 1
                checkpoint('fictional_embeddings_copied', documents=copied)
                stage = 'transport_tests'
                transport = await launch('transport-tests', [
                    sys.executable, '-m', 'pytest', 'services/dayzero/tests/integration/test_transport.py', '-q',
                ], logs)
                assert await transport.wait() == 0, 'isolated_transport_tests_failed'
                children.remove(('transport-tests', transport))
                with psycopg.connect(isolated) as db:
                    db.execute("UPDATE jobs SET status='cancelled',error='isolated_transport_cleanup' "
                        "WHERE session_id<>%s AND status IN ('pending','running')", (sid,))
                    db.execute("UPDATE sessions SET state='completed',ended=%s WHERE id<>%s", (time.time(), sid))
                checkpoint('isolated_transport_tests_passed', tests=10)
                for worker in ['ai', 'report']:
                    await launch(worker, [sys.executable, '-m', 'dayzero.workers.' + worker], logs, api_key=True)
                await call('/sessions/' + sid + '/consent', 'POST', {'accepted': True}, candidate)
                start = await call('/sessions/' + sid + '/start', 'POST', auth=candidate)
                result.update(session_id=sid, binding=start['binding'], report_binding=start['report_binding'])

                async def wait_workspace(predicate, timeout=460):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        workspace = await call('/sessions/' + sid, auth=candidate)
                        failures = [(j['agent_id'], j.get('error')) for j in workspace['jobs'] if j['status'] == 'failed']
                        if failures:
                            result['failed_jobs'] = failures
                            result['failure_workspace'] = {
                                'division': workspace['division'],
                                'parts': [{k: p[k] for k in ['id', 'number', 'version', 'owner', 'status']} for p in workspace['parts']],
                                'tasks': workspace['tasks'],
                            }
                            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                            failed_ids = {j['id'] for j in workspace['jobs'] if j['status'] == 'failed'}
                            result['failed_graphs'] = []
                            async with AsyncPostgresSaver.from_conn_string(env['CHECKPOINT_DATABASE_URL']) as saver:
                                async for saved in saver.alist({'configurable': {'thread_id': sid + ':colleague'}}, limit=100):
                                    values = saved.checkpoint.get('channel_values', {})
                                    if values.get('job_id') not in failed_ids:
                                        continue
                                    result['failed_graphs'].append({
                                        'job_id': values['job_id'], 'calls': values.get('calls'),
                                        'proposal': values.get('proposal'),
                                        'tool_messages': [m for m in values.get('messages', []) if m['content'].startswith(('도구 호출 제안:', '도구 결과 '))],
                                    })
                                    failed_ids.remove(values['job_id'])
                                    if not failed_ids:
                                        break
                            raise AssertionError('model_job_failed')
                        if predicate(workspace):
                            return workspace
                        for name, child in children:
                            if child.returncode is not None:
                                raise AssertionError('isolated_' + name + '_exited')
                        await asyncio.sleep(.5)
                    raise TimeoutError('workspace_deadline')

                stage = 'kickoff'
                await wait_workspace(lambda w: all(
                    any(j['agent_id'] == agent and j['status'] == 'done' for j in w['jobs'])
                    for agent in ['leader', 'colleague']))
                checkpoint('kickoff_complete')
                with psycopg.connect(isolated) as db:
                    previous = db.execute('SELECT started FROM sessions WHERE id=%s', (sid,)).fetchone()[0]
                    shifted = time.time() - 610
                    db.execute('UPDATE sessions SET started=%s WHERE id=%s', (shifted, sid))
                    db.execute('UPDATE events SET due=due-%s WHERE session_id=%s', (previous-shifted, sid))

                async def message(agent, text):
                    return await call('/sessions/' + sid + '/messages', 'POST', {
                        'flow': 'chat' if agent == 'chat' else 'collaboration',
                        'channel': 'assistant' if agent == 'chat' else 'team',
                        'recipients': [agent], 'text': text, 'operation_id': uuid.uuid4().hex,
                    }, candidate)

                stage = 'automatic_division'
                sent = await message('colleague',
                    '기본 분담안을 수락합니다. 1·2번은 제가, 3번은 공동, 4·5번은 동료 담당으로 확정해 주세요. '
                    '합의를 분업 도구로 기록하고 동료 담당 파트는 직접 작성해 주세요. '
                    '무료 다음 모임 알림을 제안하며 유료 기능과 세부 위치 공개는 제외합니다.')
                workspace = await wait_workspace(lambda w: w['division']['agreed'] and
                    any(r['data'].get('reply_to') == sent['id'] for r in w['records']) and
                    all(p['body'].strip() for p in w['parts'] if p['number'] in [4, 5]) and
                    len([t for t in w['tasks'] if t['owner'] == 'colleague' and t['status'] == 'done']) >= 2 and
                    not any(j['status'] in ['pending', 'running'] for j in w['jobs']))
                colleague_tasks = [t for t in workspace['tasks'] if t['owner'] == 'colleague']
                assert len(colleague_tasks) == 2, 'duplicate_division_tasks'
                checkpoint('division_auto_tasks_and_documents_complete',
                    tasks=len(colleague_tasks), manually_created_tasks=0,
                    parts=[{'number': p['number'], 'characters': len(p['body'])} for p in workspace['parts'] if p['number'] in [4, 5]])

                stage = 'ai_sources'
                sent = await message('chat', '운영 정책 문서를 검색해 올해 유료 기능과 위치 공개 원칙을 원문 근거와 함께 알려 주세요.')
                workspace = await wait_workspace(lambda w: any(r['data'].get('reply_to') == sent['id'] for r in w['records']))
                reply = next(r for r in workspace['records'] if r['data'].get('reply_to') == sent['id'])
                assert reply['data']['sources'], 'actual_mcp_sources_missing'
                footers = len(re.findall(r'(?m)^\s*참고[:：]', reply['data']['text']))
                assert footers == 1, 'duplicate_source_footer'
                checkpoint('actual_ai_source_reply', sources=len(reply['data']['sources']), source_footers=footers)
                candidate_bodies = {
                    1: '첫 참여자의 재참여율 22%를 배경으로 다음 모임 일정을 놓치는 문제를 가설로 정했습니다. 무료 알림의 재참여 효과를 2주간 작은 실험으로 확인합니다.',
                    2: '인터뷰 자료는 아직 공개되지 않아 실제 인터뷰 결과는 단정하지 않았습니다. 공개 후 일정 놓침 가설과 대상 집단을 확인할 예정입니다.',
                    3: 'AI의 운영 정책 요약을 확인하고 올해 유료 기능 출시를 제외했습니다. 동의한 사용자에게 무료 다음 모임 알림을 보내며 위치는 동네 단위로 제한합니다.',
                }
                for part in workspace['parts']:
                    if part['number'] in candidate_bodies:
                        await call('/sessions/' + sid + '/parts/' + part['id'], 'PATCH', {
                            'version': part['version'], 'operation_id': uuid.uuid4().hex,
                            'body': candidate_bodies[part['number']], 'source_message_id': reply['id'],
                        }, candidate)
                stage = 'submit_debrief_report'
                await call('/sessions/' + sid + '/submit', 'POST', auth=candidate)
                debrief = await call('/sessions/' + sid + '/debrief', auth=candidate)
                assert len(debrief['situations']) == 4, 'debrief_situations_missing'
                checkpoint('submitted_and_debrief_visible', situations=len(debrief['situations']))
                deadline = time.monotonic() + 460
                while time.monotonic() < deadline:
                    report = await call('/admin/sessions/' + sid + '/report', auth=admin)
                    if report['state'] in ['report_ready', 'failed']:
                        break
                    await asyncio.sleep(1)
                assert report['state'] == 'report_ready', 'report_' + report['state']
                assert len(report['report']['quality_comparison']) == 3, 'quality_items_missing'
                assert any(item['axis'] == 'AI 활용 역량' and item['observation'] for item in report['report']['items']), 'ai_observation_missing'
                listing = await call('/admin/sessions', auth=admin)
                assert next(row for row in listing if row['id'] == sid)['state'] == 'report_ready'
                Path('docs/evidence/codex-revision-verification-report.json').write_text(
                    json.dumps(report['report'], ensure_ascii=False, indent=2) + '\n')
                checkpoint('report_worker_and_company_api_complete', quality_items=3,
                    report_items=len(report['report']['items']))
                result['completed'] = True
    except Exception as error:
        result['failure'] = {'stage': stage, 'type': type(error).__name__,
            'detail': str(error) if isinstance(error, (AssertionError, TimeoutError, RuntimeError)) else type(error).__name__}
        result['diagnostics'] = {}
        for log in log_files:
            log.flush()
            log.seek(0)
            output = log.read()[-8000:]
            for secret in secrets:
                if secret:
                    output = output.replace(secret, '[redacted]')
            output = re.sub(r'(?i)Bearer\s+\S+|sk-[A-Za-z0-9_-]+', '[redacted]', output)
            output = re.sub(r'(amqps?|postgresql)(://)[^@\s]+@', r'\1\2[redacted]@', output)
            result['diagnostics'][Path(log.name).name] = output
        print('FAILED', result['failure'], flush=True)
    finally:
        if sid and created_db:
            try:
                with psycopg.connect(isolated) as db:
                    result['jobs'] = [
                        dict(zip(['agent', 'status', 'error', 'model_calls', 'queue_attempts'], row))
                        for row in db.execute(
                            'SELECT agent_id,status,error,model_calls,attempts FROM jobs '
                            'WHERE session_id=%s ORDER BY agent_id,sequence', (sid,)).fetchall()
                    ]
            except psycopg.Error:
                result['job_diagnostics_unavailable'] = True
        for _, child in children:
            if child.returncode is None:
                child.terminate()
        for _, child in children:
            try:
                await asyncio.wait_for(child.wait(), 10)
            except TimeoutError:
                child.kill()
                await child.wait()
        for log in log_files:
            log.close()
        if created_vhost:
            async with httpx.AsyncClient(base_url=manager_url, auth=manager_auth, timeout=5) as manager:
                response = await manager.delete('/api/vhosts/' + database)
                response.raise_for_status()
        if created_db:
            db_admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
        db_admin.close()
        result['disposable_resources_removed'] = True
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
        evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in [
        'diagnostics', 'failure_workspace', 'failed_graphs',
    ]}, ensure_ascii=False), flush=True)
    if not result['completed']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
