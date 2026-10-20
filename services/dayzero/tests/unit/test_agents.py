"""Harness/Graph tests with a synthetic model and in-memory checkpoints."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from dayzero.agents.graph import build_graph
from dayzero.agents.harness import tool_definitions, validate_tool, append_sources
from dayzero.llm.schemas import Reply, ModelError


def context(agent, kind='message'):
    return {'agent_id':agent,'binding':{'backend':'openai','model':'gpt-4o-mini','adapter_version':'1.0.2'},
        'role_version':'agents-v1','elapsed':6700,'division':{'rounds':0,'agreed':False},
        'task_brief':'공개 과제','parts':[],'tasks':[],'memory':{'version':1,'data':{}},
        'tool_calls':0,'event_version':0,'history':[{'id':'old','actor':'candidate','data':{'text':'이전 질문'}}],
        'input':{'id':'current-'+agent,'actor':'system' if kind=='work_trigger' else 'candidate',
            'kind':kind,'data':{'text':'현재 배정 업무' if kind=='work_trigger' else '업무 진행 요청'}},'channel':'team'}


async def test_three_graphs_keep_identity_threads_and_current_trigger(monkeypatch):
    captured=[];results=[]
    class Internal:
        def __init__(self,jid,token):self.agent=jid
        async def call(self,path,method='GET',body=None):
            if path=='context':return context(self.agent,'work_trigger' if self.agent=='colleague' else 'message')
            if path=='result':results.append(body);return {'status':'done'}
            return {}
    async def model(request,*args,**kwargs):
        captured.append(request)
        return Reply(kind='final',text='현재 업무를 확인하고 다음 단계를 상의하겠습니다.',tools=[]),None
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    saver=InMemorySaver();graphs={a:build_graph(a,saver) for a in ['leader','colleague','chat']}
    for agent,graph in graphs.items():
        await graph.ainvoke({'job_id':agent,'agent_id':agent},{'configurable':{'thread_id':'session:'+agent}},context={'job_token':'runtime-secret'})
    assert [r['agent_id'] for r in results]==['leader','colleague','chat']
    assert '팀장' in captured[0].messages[0]['content']
    assert '기획 동료' in captured[1].messages[0]['content']
    assert '3인칭으로 부르거나' in captured[1].messages[0]['content']
    assert 'Backend가 자동으로 등록' in captured[1].messages[0]['content']
    assert '현재 배정 업무' in captured[1].messages[-2]['content']
    assert '개인 업무용 AI' in captured[2].messages[0]['content']
    for agent,graph in graphs.items():
        state=await graph.aget_state({'configurable':{'thread_id':'session:'+agent}})
        assert state.values['agent_id']==agent and state.values['context']['agent_id']==agent
        assert 'runtime-secret' not in str(state.values)


@pytest.mark.parametrize('first_batch',[2,4])
async def test_over_budget_proposal_is_rejected_then_repaired_without_extra_tools(monkeypatch,first_batch):
    executed=[];calls=[]
    class Internal:
        def __init__(self,*args):pass
        async def call(self,path,*args):return context('colleague') if path=='context' else {}
    async def model(request,*args,**kwargs):
        calls.append(request)
        if len(calls)==1:return Reply(kind='tools',text='',tools=[{'name':'get_workspace','arguments':{}}]*first_batch),None
        if len(calls)==2:return Reply(kind='tools',text='',tools=[{'name':'get_workspace','arguments':{}}]*2),None
        return Reply(kind='final',text='현재 업무 상태를 확인했습니다.',tools=[]),None
    async def execute(*args):executed.append(args);return {'parts':[]}
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    monkeypatch.setattr('dayzero.agents.graph.execute',execute)
    await build_graph('colleague',InMemorySaver()).ainvoke({'job_id':'j','agent_id':'colleague'},
        {'configurable':{'thread_id':'budget:colleague'}},context={'job_token':'runtime'})
    assert len(executed)==2 and len(calls)==3
    repaired=calls[1] if first_batch==4 else calls[2]
    assert '제안은 실행되지 않았습니다' in repaired.messages[-2]['content']


@pytest.mark.parametrize('used',[0,2])
async def test_repeated_over_budget_batch_executes_only_remaining_prefix_and_discloses_skipped_write(monkeypatch,used):
    ctx=context('colleague');ctx['tool_calls']=used
    requests=[];executed=[];saved=[]
    proposals=[{'name':'get_workspace','arguments':{}}]*(3-used)+[
        {'name':'update_part','arguments':{'part_id':'unwritten-part','change':{'version':1,'body':'작성안'}}}]
    class Internal:
        def __init__(self,*args):pass
        async def call(self,path,method='GET',body=None):
            if path=='context':return ctx
            if path=='result':saved.append(body)
            return {}
    async def model(request,*args,**kwargs):
        requests.append(request)
        if len(requests)<=2:return Reply(kind='tools',text='문서를 수정했습니다.',tools=proposals),None
        assert request.tools==[]
        notice=next(m['content'] for m in request.messages if '한도 초과로 실행하지 않은' in m['content'])
        assert 'unwritten-part' in notice and '자동으로 이어서 처리한다고 약속하지 마세요' in notice
        return Reply(kind='final',text='업무 상태를 확인했어요. 문서 수정은 아직 처리하지 못했어요.',tools=[]),None
    async def execute(*args):
        executed.append(args)
        return {'parts':[]}
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    monkeypatch.setattr('dayzero.agents.graph.execute',execute)
    result=await build_graph('colleague',InMemorySaver()).ainvoke({'job_id':'j','agent_id':'colleague'},
        {'configurable':{'thread_id':'truncated:colleague'}},context={'job_token':'runtime'})
    assert len(requests)==3 and len(executed)==3-used and result['calls']==3
    assert [call[3].name for call in executed]==['get_workspace']*(3-used)
    assert [call[4] for call in executed]==['j:tool:'+str(n) for n in range(used,3)]
    assert saved[0]['mutation_ids']==[]
    assert '아직 처리하지 못한 작업: 문서 수정.' in saved[0]['text']
    assert '수정했습니다' not in saved[0]['text']


@pytest.mark.parametrize('footer',[
    '\n참고: 운영 정책 v1',
    '\n\n**참고:** 운영 정책 v1',
    '\n참고:\n- 운영 정책 v1',
    '\n참고: 운영 정책 v1\n참고: 운영 정책 v1',
])
def test_verified_source_footer_is_written_once(footer):
    sources=[{'id':'policy','title':'운영 정책','version':1}]
    text='올해는 무료 기능을 검토해야 해요.'
    actual=append_sources(text+footer,sources)
    assert actual==text+'\n참고: 운영 정책 v1'
    assert append_sources(actual,sources)==actual


def test_source_footer_preserves_ordinary_notes_and_distinct_versions():
    text='참고: 운영 정책을 함께 검토해 주세요.'
    sources=[{'id':'policy','title':'운영 정책','version':1},
             {'id':'policy','title':'운영 정책','version':1},
             {'id':'policy-v2','title':'운영 정책','version':2}]
    assert append_sources(text,[])==text
    assert append_sources(text,sources)==text+'\n참고: 운영 정책 v1, 운영 정책 v2'


async def test_graph_persists_one_verified_source_footer(monkeypatch):
    requests=[];saved=[]
    class Internal:
        def __init__(self,*args):pass
        async def call(self,path,method='GET',body=None):
            if path=='context':return context('colleague')
            if path=='result':saved.append(body)
            return {}
    async def model(request,*args,**kwargs):
        requests.append(request)
        if len(requests)==1:
            return Reply(kind='tools',text='',tools=[{'name':'search_documents','arguments':{'query':'운영 정책'}}]),None
        return Reply(kind='final',text='무료 기능을 검토해 주세요.\n참고: 운영 정책 v1',tools=[]),None
    async def execute(*args):
        return {'documents':[{'id':'policy','title':'운영 정책','version':1}]}
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    monkeypatch.setattr('dayzero.agents.graph.execute',execute)
    await build_graph('colleague',InMemorySaver()).ainvoke({'job_id':'j','agent_id':'colleague'},
        {'configurable':{'thread_id':'citations:colleague'}},context={'job_token':'runtime'})
    assert saved[0]['text']=='무료 기능을 검토해 주세요.\n참고: 운영 정책 v1'
    assert saved[0]['sources']==[{'id':'policy','title':'운영 정책','version':1}]


@pytest.mark.parametrize('agent',['leader','chat'])
def test_readonly_agents_cannot_gain_work_permissions(agent):
    names={t['function']['name'] for t in tool_definitions(agent)}
    assert not names.intersection({'create_task','update_task','complete_task','update_part','negotiate'})
    for name in ['create_task','update_task','complete_task','update_part','negotiate']:
        with pytest.raises(Exception) as error:validate_tool(agent,name,{'agent_id':'colleague'})
        assert error.value.status_code==403


@pytest.mark.parametrize('status',['todo','waiting'])
async def test_assigned_work_cannot_stop_at_a_future_promise(monkeypatch,status):
    ctx=context('colleague','work_trigger');ctx['input']['data'].update(trigger='assignment:t:1',target={'id':'t','part_id':'p'})
    ctx['tasks']=[{'id':'t','owner':'colleague','status':status,'version':1,'due_elapsed':6600}]
    calls=[];saved=[]
    class Internal:
        def __init__(self,*args):pass
        async def call(self,path,method='GET',body=None):
            if path=='context':return ctx
            if path=='result':saved.append(body)
            return {}
    async def model(request,*args,**kwargs):
        names={t['function']['name'] for t in request.tools}
        assert not names.intersection({'update_task','update_part'})
        if ctx['tasks'][0]['status']!='done':assert 'complete_task' in names
        calls.append(1)
        if len(calls)==1:return Reply(kind='final',text='배정 업무를 작성할 예정입니다.',tools=[]),None
        if len(calls)==2:return Reply(kind='tools',text='',tools=[{'name':'complete_task','arguments':{
            'task_id':'t','version':1,'part_version':1,'body':'실제 저장 본문','note':'완료'}}]),None
        return Reply(kind='final',text='담당 문서와 태스크를 저장했습니다.',tools=[]),None
    async def execute(*args):ctx['tasks'][0]['status']='done';return {'mutation_ids':['task-record','part-record']}
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    monkeypatch.setattr('dayzero.agents.graph.execute',execute)
    result=await build_graph('colleague',InMemorySaver()).ainvoke({'job_id':'j','agent_id':'colleague'},
        {'configurable':{'thread_id':'work:colleague'}},context={'job_token':'runtime'})
    assert len(calls)==3 and result['repairs']==1
    assert saved[0]['mutation_ids']==['task-record','part-record']


async def test_assigned_completion_binds_current_versions_and_stops_before_sibling_work(monkeypatch):
    ctx=context('colleague','work_trigger')
    ctx['input']['data'].update(trigger='assignment:t:1',target={'id':'t','part_id':'p'})
    ctx['tasks']=[{'id':'t','owner':'colleague','status':'todo','version':1,'part_id':'p'},
                  {'id':'other-task','owner':'colleague','status':'todo','version':4,'part_id':'other-part'}]
    ctx['parts']=[{'id':'p','number':4,'owner':'colleague','version':2,'body':''},
                  {'id':'other-part','number':5,'owner':'colleague','version':7,'body':''}]
    requests=[];executed=[]
    class Internal:
        def __init__(self,*args):pass
        async def call(self,path,*args):
            return ctx if path=='context' else {}
    async def model(request,*args,**kwargs):
        requests.append(request)
        if len(requests)==1:
            definition=next(t['function'] for t in request.tools if t['function']['name']=='complete_task')
            properties=definition['parameters']['properties']
            assert properties['task_id']['enum']==['t']
            assert properties['version']['enum']==[1]
            assert properties['part_version']['enum']==[2]
            return Reply(kind='tools',text='',tools=[
                {'name':'complete_task','arguments':{'task_id':'t','version':1,'part_version':2,
                                                   'body':'작성한 실제 초안','note':'완료'}},
                {'name':'complete_task','arguments':{'task_id':'other-task','version':4,'part_version':7,
                                                   'body':'다른 태스크 초안','note':'완료'}}]),None
        assert request.tools==[]
        assert '추가 작업 없이' in request.messages[-1]['content']
        return Reply(kind='final',text='배정받은 문서를 저장했습니다.',tools=[]),None
    async def execute(agent,jid,token,proposal,*args):
        executed.append(proposal.arguments['task_id'])
        ctx['tasks'][0]['status']='done'
        return {'task':{'after':dict(ctx['tasks'][0])},'mutation_ids':['saved-part','saved-task']}
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    monkeypatch.setattr('dayzero.agents.graph.execute',execute)
    result=await build_graph('colleague',InMemorySaver()).ainvoke({'job_id':'j','agent_id':'colleague'},
        {'configurable':{'thread_id':'scoped-assignment:colleague'}},context={'job_token':'runtime'})
    assert executed==['t'] and result['calls']==1 and len(requests)==2
    assert ctx['tasks'][1]['status']=='todo'


async def test_new_task_can_be_scheduled_within_the_same_model_job(monkeypatch):
    import copy
    ctx=context('colleague');ctx['elapsed']=1810
    ctx['history'][0]['data']['flow']='collaboration'
    ctx['parts']=[{'id':'p','number':4,'owner':'colleague','body':'','version':1}]
    calls=[];executed=[];saved=[]
    class Internal:
        def __init__(self,*args):pass
        async def call(self,path,method='GET',body=None):
            if path=='context':return copy.deepcopy(ctx)
            if path=='result':saved.append(body)
            return {}
    async def model(request,*args,**kwargs):
        calls.append(request)
        if len(calls)==1:return Reply(kind='tools',text='',tools=[{'name':'create_task','arguments':{
            'title':'10분 뒤 비교 확인','owner':'colleague','part_id':'p','source_id':'old'}}]),None
        if len(calls)==2:
            definition=next(t for t in request.tools if t['function']['name']=='update_task')
            assert definition['function']['parameters']['properties']['task_id']['enum']==['new-task']
            return Reply(kind='tools',text='',tools=[{'name':'update_task','arguments':{
                'task_id':'new-task','change':{'version':1,'status':'waiting','due_elapsed':2410}}}]),None
        return Reply(kind='final',text='확인할 업무와 약속 시점을 등록했습니다.',tools=[]),None
    async def execute(agent,jid,token,proposal,*args):
        executed.append(proposal.name)
        if proposal.name=='create_task':ctx['tasks']=[{'id':'new-task','owner':'colleague','status':'todo','version':1}]
        else:ctx['tasks'][0].update(status='waiting',version=2,due_elapsed=2410)
        return {'task':copy.deepcopy(ctx['tasks'][0]),'mutation_ids':['mutation-'+str(len(executed))]}
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    monkeypatch.setattr('dayzero.agents.graph.execute',execute)
    result=await build_graph('colleague',InMemorySaver()).ainvoke({'job_id':'j','agent_id':'colleague'},
        {'configurable':{'thread_id':'promise:colleague'}},context={'job_token':'runtime'})
    assert executed==['create_task','update_task'] and len(calls)==3
    assert result['context']['tasks'][0]['status']=='waiting'
    assert saved[0]['mutation_ids']==['mutation-1','mutation-2']


async def test_event_reload_keeps_current_job_mutation_proof(monkeypatch):
    ctx=context('colleague');ctx['mutation_ids']=['already-saved-in-this-job']
    saved=[]
    class Internal:
        def __init__(self,*args):pass
        async def call(self,path,method='GET',body=None):
            if path=='context':return ctx
            if path=='result':saved.append(body)
            return {}
    async def model(*args,**kwargs):return Reply(kind='final',text='앞서 담당 문서를 저장했습니다.',tools=[]),None
    monkeypatch.setattr('dayzero.agents.graph.Internal',Internal)
    monkeypatch.setattr('dayzero.agents.graph.complete',model)
    await build_graph('colleague',InMemorySaver()).ainvoke({'job_id':'j','agent_id':'colleague'},
        {'configurable':{'thread_id':'reload:colleague'}},context={'job_token':'runtime'})
    assert saved[0]['mutation_ids']==['already-saved-in-this-job']
