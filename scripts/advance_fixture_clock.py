"""Advance only the explicitly named compressed synthetic fixture, never a real run."""
import sys
import time
from sqlalchemy import select
from dayzero.backend.db import DB,Session,Company,Event
from dayzero.backend.domain import tick_session

sid,seconds=sys.argv[1],float(sys.argv[2])
assert 0<=seconds<=7205
with DB.begin() as db:
    session=db.get(Session,sid)
    company=db.get(Company,session.company_id)
    assert company.name=='개정 압축 검증 기업' and company.email.startswith('compressed-') and company.email.endswith('@example.com')
    assert session.alias.startswith('개정 압축 합성 검증 ') and session.state=='active'
    current=time.time()-session.started
    assert seconds>=current,'clock must move forward'
    shift=session.started-(time.time()-seconds)
    session.started-=shift
    for event in db.scalars(select(Event).where(Event.session_id==sid)):event.due-=shift
    tick_session(db,session)
print('compressed fixture clock advanced')
