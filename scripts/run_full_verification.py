"""Run the synthetic 120-minute browser fixture with temporary local Workers.

Requires the selected model credential in the environment. Only the fictional
PRD corpus and new synthetic interactions in full_session.mjs reach the model.
All existing pending work must be stopped first, so unrelated data is excluded.
"""
import asyncio
import os
import sys
from sqlalchemy import select
from dayzero.backend.db import DB, Job, Session, Company


def preview_fixture(session, company):
    return company.email.endswith('@example.com') and (
        company.name=='UI 피드백용 합성 기업' and company.email.startswith('preview-') and session.alias=='UI 확인'
        or company.name=='UI 레이아웃 검증 전용 기업' and company.email.startswith('ui-') and session.alias=='UI 확인용')


def preflight():
    with DB.begin() as db:
        for job in db.scalars(select(Job).where(Job.status.in_(['pending','running']))):
            session=db.get(Session,job.session_id)
            assert preview_fixture(session,db.get(Company,session.company_id)), 'unexpected pending work'
        for session in db.scalars(select(Session).where(Session.state=='active')):
            assert preview_fixture(session,db.get(Company,session.company_id)), 'unexpected active session'
    assert os.environ.get('OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/')=='https://api.openai.com/v1'


async def main():
    preflight()
    children=[]
    try:
        for module in ['dayzero.workers.ai','dayzero.workers.report']:
            children.append(await asyncio.create_subprocess_exec(sys.executable,'-m',module,
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL))
        browser=await asyncio.create_subprocess_exec('node','scripts/full_session.mjs')
        children.append(browser)
        result=await browser.wait()
        if result:raise SystemExit(result)
    finally:
        for child in children:
            if child.returncode is None:child.terminate()
        for child in children:
            try:await asyncio.wait_for(child.wait(),10)
            except TimeoutError:child.kill();await child.wait()


if __name__=='__main__':asyncio.run(main())
