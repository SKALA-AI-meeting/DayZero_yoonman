"""Detached comparison of one known synthetic snapshot; never rebind a session.

The saved session/report/job and their original model provenance are read only.
Only the fictional Looplab corpus and synthetic actions enter model messages.
"""
import asyncio
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from dayzero.backend.db import DB, Session, Company
from dayzero.llm.client import bind
from dayzero.llm.schemas import Binding, ModelError
from dayzero.reporting.build import generate, validate_report
from dayzero.shared.config import settings

SID = '4967a424-5084-4192-ae67-385329265568'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def main():
    api_mini = sys.argv[1:] == ['--api-mini']
    assert sys.argv[1:] in [[], ['--api-mini']]
    assert os.environ.get('OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/')=='https://api.openai.com/v1'
    with DB.begin() as db:
        session = db.get(Session, SID)
        company = db.get(Company, session.company_id)
        assert company.name == '합성 연동 검증 기업'
        assert company.email.startswith('live-') and company.email.endswith('@example.com')
        assert session.alias == '합성 실제 모델 검증' and session.state == 'failed'
        original = copy.deepcopy(session.snapshot)
    before = fingerprint(original)
    snapshot = copy.deepcopy(original)
    snapshot['report_binding'] = (Binding(backend='openai',model='gpt-5.4-mini-2026-03-17') if api_mini else bind(settings(), report=True)).model_dump()
    assert snapshot['report_binding']['backend'] == ('openai' if api_mini else 'codex_cli')
    prefix='revision-gpt54mini-report' if api_mini else 'revision-cli-report'
    result = {'session_id': SID, 'synthetic_fixture': True, 'detached_comparison': True,
        'snapshot_sha256': before, 'agent_binding': snapshot['binding'],
        'report_binding': snapshot['report_binding'], 'completed': False,
        'scope': 'report inference and strict evidence validation; no live session or 120-minute claim'}
    started = time.monotonic()
    attempts = 0
    async def reserve():
        nonlocal attempts
        if attempts >= 3:
            raise ModelError('model_budget_exhausted')
        attempts += 1
    try:
        report = await generate(snapshot, reserve=reserve)
        validate_report(report, snapshot)
        result.update(completed=True, observed_items=[r['item'] for r in report['items'] if r['observation']],
            quality_items=len(report['quality_comparison']), prompt_turns=len(report['prompt_threads']),
            utilization_links=len(report['utilization_links']), semantic_review='pending')
        Path('docs/evidence/'+prefix+'-content.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    except ModelError as error:
        result['error'] = error.code
    with DB.begin() as db:
        assert fingerprint(db.get(Session, SID).snapshot) == before
    result.update(seconds=round(time.monotonic()-started, 2), model_calls=attempts,
        original_snapshot_unchanged=True)
    Path('docs/evidence/'+prefix+'-comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
