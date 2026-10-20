import copy
import pytest
from dayzero.reporting.build import baseline,evidence_catalog,validate_report
from dayzero.llm.schemas import ModelError


def snapshot():
    return {'version':1,'binding':{'backend':'openai','model':'gpt-4o-mini','adapter_version':'1.0.0','cli_version':None},
      'started':1000,'ended':1200,'events':[],'parts':[], 'records':[]}


def test_distinguish_no_opportunity_and_ai_unused():
    rows=baseline(snapshot())
    assert {r['status'] for r in rows if r['axis']=='협업 태도'}=={'관찰 기회 없음'}
    assert {r['status'] for r in rows if r['axis']=='AI 활용 역량'}=={'사용하지 않음'}
    assert {r['status'] for r in rows if r['axis']=='업무 수행 역량'}=={'관련 행동이 기록되지 않음'}


def test_stored_does_not_prove_delivery():
    s=snapshot();s['events']=[{'key':'help','stored_at':1010,'received_at':None}]
    assert next(r for r in baseline(s) if r['item']=='협력 태도')['status']=='기록 불충분'
    s['events'][0]['received_at']=1020
    assert next(r for r in baseline(s) if r['item']=='협력 태도')['status']=='관련 행동이 기록되지 않음'


def test_quote_and_actor_tampering_rejected():
    s=snapshot();s['records']=[{'id':'m1','kind':'message','actor':'colleague','at':1010,'elapsed':10,
       'data':{'flow':'collaboration','text':'동료가 작성한 초안'}}]
    evidence=evidence_catalog(s)['m1']
    report={'version':1,'binding':s['binding'],'items':baseline(s)}
    report['items'][0]['evidence']=[copy.deepcopy(evidence)]
    validate_report(report,s)
    report['items'][0]['evidence'][0]['actor']='candidate'
    with pytest.raises(ModelError,match='mismatch'):validate_report(report,s)


def test_incomplete_chat_is_not_negative_or_unused():
    s=snapshot();s['records']=[{'id':'q','kind':'message','actor':'candidate','at':1010,'elapsed':10,
        'data':{'flow':'chat','text':'요약 요청'}}]
    s['record_gaps']=[{'flow':'chat','input_id':'q','status':'failed'}]
    assert {r['status'] for r in baseline(s) if r['axis']=='AI 활용 역량'}=={'기록 불충분'}


def test_explicit_use_and_matching_expression_do_not_imply_verification():
    from dayzero.reporting.build import utilization_links
    s=snapshot();s['records']=[
      {'id':'answer','kind':'message','actor':'assistant','at':1010,'elapsed':10,
       'data':{'flow':'chat','text':'이번 분기에는 무료 다음 모임 알림 기능을 검토합니다.'}},
      {'id':'use','kind':'message','actor':'candidate','at':1020,'elapsed':20,
       'data':{'flow':'collaboration','text':'무료 알림으로 바꾸고 근거를 보완하겠습니다.','source_message_id':'answer'}},
      {'id':'match','kind':'message','actor':'candidate','at':1030,'elapsed':30,
       'data':{'flow':'collaboration','text':'이번 분기에는 무료 다음 모임 알림 기능을 검토합니다.'}},
      {'id':'colleague','kind':'message','actor':'colleague','at':1040,'elapsed':40,
       'data':{'flow':'collaboration','text':'이번 분기에는 무료 다음 모임 알림 기능을 검토합니다.'}}]
    links=utilization_links(s)
    assert [x['relation'] for x in links]==['활용을 명시함','표현이 일치함']
    assert all(x['verified'] is None for x in links)
    assert links[0]['answer']==evidence_catalog(s)['answer']
    report={'version':1,'binding':s['binding'],'items':baseline(s),'utilization_links':copy.deepcopy(links)}
    validate_report(report,s)
    report['utilization_links'][0]['candidate_action']['actor']='assistant'
    with pytest.raises(ModelError,match='invalid_utilization_evidence'):validate_report(report,s)


def test_chat_request_is_not_collaboration_or_submitted_work():
    from dayzero.reporting.build import candidate_action_in_window
    row={'axis':'협업 태도','window':[0,200]}
    evidence={'actor':'candidate','elapsed':20,'kind':'message','data':{'flow':'chat','text':'도움을 어떻게 거절할까요?'}}
    assert not candidate_action_in_window(row,evidence)
    row['axis']='업무 수행 역량'
    assert not candidate_action_in_window(row,evidence)
    row['axis']='AI 활용 역량'
    assert candidate_action_in_window(row,evidence)


async def test_report_accepts_json_code_block_without_weakening_evidence_checks(monkeypatch):
    from dayzero.reporting.build import generate
    from dayzero.llm.schemas import Reply
    async def answer(*args,**kwargs):
        return Reply(kind='final',text='```json\n[]\n```',tools=[]),None
    monkeypatch.setattr('dayzero.reporting.build.complete',answer)
    report=await generate(snapshot())
    assert len(report['items'])==10
    assert not any(row['observation'] or row['evidence'] for row in report['items'])


async def test_report_repairs_wrong_actor_evidence_before_publication(monkeypatch):
    import json
    from dayzero.reporting.build import generate
    from dayzero.llm.schemas import Reply
    s=snapshot();s['records']=[
        {'id':'q','kind':'message','actor':'candidate','at':1010,'elapsed':10,
         'data':{'flow':'chat','text':'정책을 요약해 주세요.'}},
        {'id':'a','kind':'message','actor':'assistant','at':1020,'elapsed':20,
         'data':{'flow':'chat','text':'정책 요약 초안'}}]
    calls=[]
    async def answer(request,*args,**kwargs):
        calls.append(request)
        ids=['a'] if len(calls)==1 else ['q']
        return Reply(kind='final',text=json.dumps([{'item':'위임 판단',
            'observation':'정책 요약을 요청함','candidate_ids':ids,'supporting_ids':[]}]),tools=[]),None
    monkeypatch.setattr('dayzero.reporting.build.complete',answer)
    report=await generate(s)
    assert len(calls)==2
    assert 'candidate_evidence_required' in calls[1].messages[-1]['content']
    row=next(r for r in report['items'] if r['item']=='위임 판단')
    assert [e['id'] for e in row['evidence']]==['q']


def revised_snapshot():
    s=snapshot();s.update(version=2,contract={'roles':'agents-v1','quality':'quality-v1'},scenario_version='looplab-v1')
    s['parts']=[{'id':str(i),'number':i,'title':'파트 '+str(i),'body':'무료 알림 제안 '+str(i),'version':2,'owner':'shared','status':'todo'} for i in range(1,6)]
    s['records']=[{'id':'edit','kind':'part','actor':'colleague','at':1010,'elapsed':10,'data':{'before':{**s['parts'][0],'body':''},'after':s['parts'][0]}},
        {'id':'q','kind':'message','actor':'candidate','at':1020,'elapsed':20,'data':{'flow':'chat','text':'정책에 맞는 무료 알림을 요약해 주세요.'}}]
    return s


def revised_output(s, observations=None):
    import json
    return json.dumps({'observations':observations or [],'quality':[{
        'item':item,'rationale':'최종본에 무료 알림 제안이 기재되어 있습니다. 적용 범위는 확인이 필요합니다.',
        'supporting_final_ids':['final:'+p['id']+':v'+str(p['version']) for p in s['parts']],'conflicting_final_ids':[],'checks':['정책 원문과 대조']} for item in ['요구사항 반영','근거 활용','실행 가능성']]},ensure_ascii=False)


def test_revised_report_requires_observations_and_quality_without_wrong_attribution():
    from dayzero.reporting.build import assemble_report,utilization_links
    s=revised_snapshot();catalog=evidence_catalog(s)
    assert catalog['final:1:v2']['actor']=='colleague'
    with pytest.raises(ModelError,match='empty_report_observations'):
        assemble_report(revised_output(s),None,s,catalog,utilization_links(s))
    output=revised_output(s,[{'item':'위임 판단','observation':'무료 알림 요약을 요청함','candidate_ids':['q'],'supporting_ids':[]}])
    report=assemble_report(output,None,s,catalog,utilization_links(s))
    assert len(report['final_parts'])==5 and len(report['quality_comparison'])==3
    assert report['quality_comparison'][0]['supporting'][0]['actor']=='colleague'
    assert report['process_evidence']==[catalog['edit'],catalog['q']]
    tampered=copy.deepcopy(report);tampered['process_evidence'][1]['quote']='변조된 요청'
    with pytest.raises(ModelError,match='invalid_report_process'):validate_report(tampered,s)
    assert not any(r['observation'] for r in report['items'] if r['axis']=='업무 수행 역량')
    report['quality_comparison'][0]['supporting'][0]['quote']='만들어낸 최종본'
    with pytest.raises(ModelError,match='invalid_quality_evidence'):validate_report(report,s)


def test_followup_utilization_is_eligible_and_future_requirements_are_not_applied():
    from dayzero.reporting.build import candidate_action_in_window,session_conditions
    s=revised_snapshot();s['events']=[{'key':'change','stored_at':1100,'received_at':None}]
    assert session_conditions(s)['target']=='첫 참여자'
    s['events'][0]['received_at']=1110
    assert session_conditions(s)['target']=='3개월 넘게 모임에 안 나온 사용자'
    row={'axis':'AI 활용 역량','window':[0,200]}
    assert candidate_action_in_window(row,{'kind':'part','actor':'candidate','elapsed':100,
        'data':{'source_message_id':'ai-response','after':{'body':'검토 후 반영'}}})


async def test_validation_exhaustion_keeps_original_cause_without_queue_budget_masking(monkeypatch):
    import json
    from dayzero.reporting.build import generate
    from dayzero.llm.schemas import Reply
    s=snapshot();s['records']=[{'id':'q','kind':'message','actor':'candidate','at':1010,'elapsed':10,
        'data':{'flow':'chat','text':'정책 요약'}},{'id':'a','kind':'message','actor':'assistant','at':1020,'elapsed':20,
        'data':{'flow':'chat','text':'정책 답변'}}]
    attempts=[]
    async def invalid(*args,**kwargs):
        attempts.append(1)
        return Reply(kind='final',text=json.dumps([{'item':'위임 판단','observation':'요약을 요청함',
            'candidate_ids':['a'],'supporting_ids':[]}]),tools=[]),None
    monkeypatch.setattr('dayzero.reporting.build.complete',invalid)
    with pytest.raises(ModelError,match='candidate_evidence_required') as failure:
        await generate(s)
    assert len(attempts)==3 and failure.value.retryable is False


def test_revised_report_rejects_missing_ai_axis_partial_final_coverage_and_judgment():
    import json
    from dayzero.reporting.build import assemble_report,utilization_links
    s=revised_snapshot();cat=evidence_catalog(s)
    base=json.loads(revised_output(s,[{'item':'위임 판단','observation':'정책에 맞는 무료 알림 요약을 요청함','candidate_ids':['q'],'supporting_ids':[]}]))
    partial=copy.deepcopy(base)
    for row in partial['quality']:row['supporting_final_ids']=['final:1:v2']
    with pytest.raises(ModelError,match='final_output_not_covered'):
        assemble_report(json.dumps(partial),None,s,cat,utilization_links(s))
    judged=copy.deepcopy(base);judged['quality'][0]['rationale']='여러 차례 수정하여 실제로 실행 가능하게 만듦'
    with pytest.raises(ModelError,match='unsupported_quality_judgment'):
        assemble_report(json.dumps(judged),None,s,cat,utilization_links(s))
    good=assemble_report(json.dumps(base),None,s,cat,utilization_links(s))
    row=next(r for r in good['items'] if r['item']=='위임 판단');row.update(observation='',evidence=[],status='관련 행동이 기록되지 않음')
    with pytest.raises(ModelError,match='ai_observation_required'):validate_report(good,s)


async def test_short_ids_do_not_relax_actor_validation(monkeypatch):
    import json
    from dayzero.reporting.build import generate
    from dayzero.llm.schemas import Reply
    s=snapshot();s['records']=[{'id':'original-request','kind':'message','actor':'candidate','at':1010,'elapsed':10,
        'data':{'flow':'chat','text':'정책을 요약해 주세요.'}}, {'id':'original-answer','kind':'message','actor':'assistant','at':1020,'elapsed':20,
        'data':{'flow':'chat','text':'정책 응답'}}]
    attempts=[]
    async def model(request,*args,**kwargs):
        payload=json.loads(request.messages[1]['content']);attempts.append(payload)
        primary=payload['candidate_evidence'][0]['id'];support=payload['supporting_evidence'][0]['id']
        assert primary.startswith('C') and support.startswith('S')
        return Reply(kind='final',text=json.dumps([{'item':'위임 판단','observation':'정책 요약을 요청함',
            'candidate_ids':[support if len(attempts)==1 else primary],'supporting_ids':[]}]),tools=[]),None
    monkeypatch.setattr('dayzero.reporting.build.complete',model)
    report=await generate(s)
    assert len(attempts)==2
    row=next(r for r in report['items'] if r['item']=='위임 판단')
    assert row['evidence'][0]['id']=='original-request' and row['evidence'][0]['actor']=='candidate'


async def test_report_uses_frozen_report_binding_and_preserves_agent_provenance(monkeypatch):
    import json
    from dayzero.reporting.build import generate
    from dayzero.llm.schemas import Binding,Reply
    s=snapshot();s['binding']=Binding(backend='openai',model='gpt-4o-mini').model_dump()
    s['report_binding']=Binding(backend='codex_cli',model='gpt-5.6-sol',cli_version='codex-cli 0.153.4').model_dump()
    async def answer(request,*args,**kwargs):
        assert request.binding.model_dump()==s['report_binding']
        assert request.tools==[]
        return Reply(kind='final',text=json.dumps([]),tools=[]),None
    monkeypatch.setattr('dayzero.reporting.build.complete',answer)
    report=await generate(s)
    assert report['binding']==s['report_binding'] and report['agent_binding']==s['binding']
    report['binding']=s['binding']
    with pytest.raises(ModelError,match='invalid_report_binding'):validate_report(report,s)
    report['binding']=s['report_binding'];report['agent_binding']=s['report_binding']
    with pytest.raises(ModelError,match='invalid_agent_binding'):validate_report(report,s)
