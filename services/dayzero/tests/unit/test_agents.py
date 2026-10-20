"""Harness/Graph tests with a synthetic model and in-memory checkpoints."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from dayzero.agents.graph import build_graph
from dayzero.agents.harness import tool_definitions, validate_tool
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
