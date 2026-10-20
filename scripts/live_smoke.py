"""Explicitly synthetic Looplab fixture + real selected-model integration.

Only the fictional scenario documents supplied in PRD section 6 and newly
created synthetic messages are sent to the model. No existing customer records,
passwords, invite credentials or job tokens are included in model messages.
"""
import asyncio
import json
import time
import sys
import os
import subprocess
import uuid
from urllib.parse import urlparse
from pathlib import Path
import httpx
from dayzero.shared.config import settings


async def until(client, path, auth, condition, timeout=460):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        r=await client.get(path,headers=auth);r.raise_for_status();value=r.json()
        if condition(value):return value
        await asyncio.sleep(1)
    raise TimeoutError('live verification deadline')


async def main():
    async with httpx.AsyncClient(base_url=settings().backend_url,timeout=20) as c:
        reg=await c.post('/auth/register',json={'email':'live-'+str(uuid.uuid4())+'@example.com','password':str(uuid.uuid4()),'name':'합성 연동 검증 기업'})
        reg.raise_for_status();admin={'Authorization':'Bearer '+reg.json()['token']}
        inv=await c.post('/admin/invitations',headers=admin,json={'alias':'합성 실제 모델 검증','email':'synthetic@example.com'})
        inv.raise_for_status();sid=inv.json()['id'];token=urlparse(inv.json()['url']).path.rsplit('/',1)[1];auth={'Authorization':'Bearer '+token}
        (await c.post('/sessions/'+sid+'/consent',headers=auth,json={'accepted':True})).raise_for_status()
        start=await c.post('/sessions/'+sid+'/start',headers=auth);start.raise_for_status()
        print('session',sid,'binding',start.json()['binding'],flush=True)
        w=await until(c,'/sessions/'+sid,auth,lambda w:all(any(j['agent_id']==a and j['status']=='done' for j in w['jobs']) for a in ['leader','colleague']))
        answers=[]
        for agent,channel,message in [('chat','assistant','운영 정책 문서를 검색해서 올해 유료 기능을 출시할 수 있는지 원문 근거와 문서 이름을 알려 주세요.'),
                                     ('colleague','dm','운영 정책을 검색해서 올해 유료 기능과 위치 정보 공개 원칙을 확인해 주세요.'),
                                     ('leader','team','팀장님, 운영 정책에 맞춰 무료 알림을 검토하려고 합니다. 운영 정책 원문을 검색해 확인 부탁드립니다.'),
                                     ('chat','assistant','앞선 정책 요약을 확인했습니다. 위치 공개는 동네 단위로 제한해 무료 알림 초안을 짧게 제안해 주세요.')]:
            sent=await c.post('/sessions/'+sid+'/messages',headers=auth,json={
                'flow':'chat' if agent=='chat' else 'collaboration','channel':channel,'recipients':[agent],
                'text':message,'operation_id':str(uuid.uuid4())})
            sent.raise_for_status();mid=sent.json()['id']
            w=await until(c,'/sessions/'+sid,auth,lambda w:any(r['data'].get('reply_to')==mid for r in w['records']) or any(j['input_id']==mid and j['status']=='failed' for j in w['jobs']))
            replies=[r for r in w['records'] if r['data'].get('reply_to')==mid]
            assert replies,[(j['agent_id'],j['status'],j['error']) for j in w['jobs'] if j['input_id']==mid]
            assert replies[0]['actor']==('assistant' if agent=='chat' else agent)
            if len(answers)<3:
                assert replies[0]['data']['sources'],'Live answer must use actual MCP sources'
            answers.append(replies[0])
            print('live_reply',agent,'sources',replies[0]['data']['sources'],flush=True)
        part=w['parts'][3]
        assigned=await c.post('/sessions/'+sid+'/tasks',headers=auth,json={
            'title':'무료 알림의 비교 기준 정리','owner':'colleague','part_id':part['id'],
            'note':'정책 문서를 검색해 유료 기능 제외·동네 단위 공개 원칙을 기준으로 가상의 기능 비교 초안을 4번 파트에 저장해 주세요. complete_task로 문서와 완료 상태를 함께 저장하고 실제 출처를 밝혀 주세요.',
            'operation_id':str(uuid.uuid4())})
        assigned.raise_for_status();tid=assigned.json()['id']
        w=await until(c,'/sessions/'+sid,auth,lambda w:(any(t['id']==tid and t['status']=='done' for t in w['tasks']) and any(r['data'].get('mutation_ids') for r in w['records'] if r['actor']=='colleague')) or any(j['status']=='failed' for j in w['jobs']) or any(j['input_id']==next((r['id'] for r in w['records'] if r['kind']=='work_trigger' and r['data'].get('target',{}).get('id')==tid),'') and j['status']=='done' for j in w['jobs']))
        assert next(t for t in w['tasks'] if t['id']==tid)['status']=='done',[(j['agent_id'],j['error']) for j in w['jobs']]
        changes=[r for r in w['records'] if r['kind']=='part' and r['actor']=='colleague']
        assert changes and any(r['data'].get('mutation_ids') for r in w['records'] if r['actor']=='colleague')
        share=await c.post('/sessions/'+sid+'/messages',headers=auth,json={
            'flow':'collaboration','channel':'team','recipients':['colleague'],
            'text':'개인 AI의 정책 요약을 운영 정책 원문과 대조했습니다. 무료 알림만 선택해 반영하겠습니다.',
            'source_message_id':answers[0]['id'],'operation_id':str(uuid.uuid4())})
        share.raise_for_status();mid=share.json()['id']
        w=await until(c,'/sessions/'+sid,auth,lambda w:any(r['data'].get('reply_to')==mid for r in w['records']) or any(j['input_id']==mid and j['status']=='failed' for j in w['jobs']))
        assert any(r['data'].get('reply_to')==mid for r in w['records']), [(j['agent_id'],j['error']) for j in w['jobs'] if j['input_id']==mid]
        p=w['parts'][0]
        saved=await c.patch('/sessions/'+sid+'/parts/'+p['id'],headers=auth,json={
            'version':p['version'],'source_message_id':answers[0]['id'],'operation_id':str(uuid.uuid4()),'body':'운영 정책: 올해는 유료 기능을 출시하지 않는다. 무료 다음 모임 알림을 제안합니다.'})
        saved.raise_for_status()
        if start.json()['binding']['backend'] == 'codex_cli':
            await asyncio.sleep(30)
        (await c.post('/sessions/'+sid+'/submit',headers=auth)).raise_for_status()
        report=await until(c,'/admin/sessions/'+sid+'/report',admin,lambda r:r['state'] in ['report_ready','failed'])
        assert report['state']=='report_ready',report['state']
        observed = [r['item'] for r in report['report']['items'] if r['observation']]
        assert observed, 'Existing candidate actions must produce supported report observations'
        assert any(r['axis']=='AI 활용 역량' and r['observation'] for r in report['report']['items']), 'AI requests require an evidenced AI observation'
        assert len(report['report']['quality_comparison'])==3
        assert len(report['report']['prompt_threads'])==2
        assert report['report']['utilization_links']
        print('report_ready',len(report['report']['items']),'items',flush=True)
        output={'session_id':sid,'binding':start.json()['binding'],'report_binding':start.json().get('report_binding'),'synthetic_fixture':True,'actual_model':True,
                'three_agents_with_actual_mcp_sources':True,'colleague_task_and_document_saved':True,
                'quality_items':len(report['report']['quality_comparison']),'prompt_turns':len(report['report']['prompt_threads']),
                'utilization_links':len(report['report']['utilization_links']),'report_items':len(report['report']['items']),
                'observed_items':observed,
                'elapsed_seconds':time.time()-w['started'],'full_120_minutes':False}
        Path('docs/evidence/revision-live-smoke.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
        Path('docs/evidence/revision-live-report-content.json').write_text(json.dumps(report['report'],ensure_ascii=False,indent=2)+'\n')


async def retry_synthetic_report(sid):
    from sqlalchemy import select
    from dayzero.backend.db import DB, Session, Company, Job
    from dayzero.backend.security import issue
    with DB.begin() as db:
        session=db.get(Session,sid)
        assert session and session.state=='failed' and session.snapshot
        company=db.get(Company,session.company_id)
        assert company.name=='합성 연동 검증 기업' and company.email.startswith('live-') and company.email.endswith('@example.com')
        assert session.alias=='합성 실제 모델 검증'
        snapshot=session.snapshot
        admin={'Authorization':'Bearer '+issue(company.id,company.id)}
    async with httpx.AsyncClient(base_url=settings().backend_url,timeout=20) as client:
        (await client.post('/admin/sessions/'+sid+'/report/retry',headers=admin)).raise_for_status()
        result=await until(client,'/admin/sessions/'+sid+'/report',admin,lambda r:r['state'] in ['report_ready','failed'])
        assert result['state']=='report_ready', 'strict report retry failed'
        report=result['report']
        from dayzero.reporting.build import validate_report
        validate_report(report,snapshot)
        assert any(r['axis']=='AI 활용 역량' and r['observation'] for r in report['items'])
        actors={r['actor'] for r in snapshot['records'] if r['kind']=='message' and r['data'].get('sources')}
        assert {'assistant','leader','colleague'} <= actors
        output={'session_id':sid,'binding':snapshot['binding'],'synthetic_fixture':True,'actual_model':True,
            'three_agents_with_actual_mcp_sources':True,'colleague_task_and_document_saved':True,
            'quality_items':len(report['quality_comparison']),'prompt_turns':len(report['prompt_threads']),
            'utilization_links':len(report['utilization_links']),'report_items':len(report['items']),
            'observed_items':[r['item'] for r in report['items'] if r['observation']],
            'full_120_minutes':False,'actual_report_retry':True}
        Path('docs/evidence/revision-live-smoke.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(output,ensure_ascii=False),flush=True)


async def isolated_run():
    # This verification starts only after all newly created test work is stopped.
    # No existing pending job may be sent to an external model.
    from run_full_verification import preflight
    preflight()
    workers=[]
    try:
        for module in ['dayzero.workers.ai','dayzero.workers.report']:
            workers.append(await asyncio.create_subprocess_exec(sys.executable,'-m',module,
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL))
        if len(sys.argv)==3 and sys.argv[1]=='--retry-report':
            await retry_synthetic_report(sys.argv[2])
        else:
            await main()
    finally:
        for worker in workers:
            if worker.returncode is None:worker.terminate()
        for worker in workers:
            try:await asyncio.wait_for(worker.wait(),10)
            except TimeoutError:worker.kill();await worker.wait()


if __name__=='__main__':asyncio.run(isolated_run())
