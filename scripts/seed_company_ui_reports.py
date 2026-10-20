"""Inject explicitly synthetic report results for the company browser fixture.

Only newly invited sessions in named synthetic companies are accepted. Jobs are
made terminal in the same transaction, so running model Workers cannot consume
these UI fixtures. This is not evidence of model report generation.
"""
import json
import sys
import time
from sqlalchemy import select
from dayzero.backend.db import DB, Session, Company, Part, Job
from dayzero.backend.domain import PARTS, BRIEF, CONTRACT, journal, close_session, serialize_part
from dayzero.llm.client import bind
from dayzero.reporting.build import assemble_report, evidence_catalog, utilization_links
from dayzero.shared.config import settings


def seed(fixtures):
    with DB.begin() as db:
        for fixture in fixtures:
            session=db.scalar(select(Session).where(Session.id==fixture['id']).with_for_update())
            company=db.get(Company,session.company_id)
            assert (company.name=='기업 화면 합성 검증' and company.email.startswith('ui-company-')) or (
                company.name=='UI 피드백용 합성 기업' and company.email.startswith('preview-'))
            assert company.email.endswith('@example.com') and session.state=='invited'
            now=time.time()
            session.state,session.started,session.consented='active',now-300,True
            session.binding=bind(settings()).model_dump()
            session.report_binding=bind(settings(),report=True).model_dump()
            session.contract=dict(CONTRACT)
            journal(db,session,'message','leader',{'text':BRIEF,'flow':'collaboration','channel':'team'},'ui:brief',session.started)
            parts=[]
            for i,title in enumerate(PARTS,1):
                part=Part(session_id=session.id,number=i,title=title,owner='candidate')
                db.add(part);parts.append(part)
            db.flush()
            part=parts[0];before=serialize_part(part)
            part.body,part.version,part.status=fixture['body'],2,'done'
            db.flush()
            action=journal(db,session,'part','candidate',{'before':before,'after':serialize_part(part)},'ui:edit',session.started+60)
            close_session(db,session,now)
            job=db.scalar(select(Job).where(Job.session_id==session.id,Job.flow=='reports'))
            state=fixture.get('state','report_ready')
            assert state in ['report_ready','completed','failed']
            if state=='report_ready':
                output={'observations':[{'item':'요구사항 반영','observation':'합성 화면 예시의 기획안 문구를 저장한 기록입니다.',
                    'candidate_ids':[action.id],'supporting_ids':[]}],
                    'quality':[{'item':item,'rationale':'합성 UI 검증 자료입니다. 최종본에 기재된 내용과 추가 확인할 사항을 함께 봅니다.',
                        'supporting_final_ids':['final:'+part.id+':v2'],'conflicting_final_ids':[],
                        'checks':['실제 모델 생성 결과가 아닌 화면 검증 예시']} for item in ['요구사항 반영','근거 활용','실행 가능성']]}
                snapshot=session.snapshot
                session.report=assemble_report(json.dumps(output),{'synthetic_ui_fixture':True,'calls':[]},snapshot,
                    evidence_catalog(snapshot),utilization_links(snapshot))
            session.state=state
            job.status='done' if state=='report_ready' else 'cancelled'
            job.error=None if state=='report_ready' else 'synthetic_ui_state_fixture'
            job.result={'synthetic_ui_fixture':True,'binding':session.report_binding}


if __name__=='__main__':
    seed(json.loads(sys.argv[1]))
