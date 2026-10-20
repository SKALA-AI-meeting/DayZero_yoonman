import asyncio
import json
from types import SimpleNamespace as NS
import pytest
from pydantic import ValidationError
from dayzero.llm.schemas import Binding, Request, Reply, ModelError
from dayzero.llm.openai import OpenAIAdapter
from dayzero.llm.codex_cli import CodexCLIAdapter
from dayzero.shared.config import Settings
from dayzero.agents.harness import validate_tool


def request(backend='openai', **kw):
    return Request(binding=Binding(backend=backend,model='gpt-4o-mini' if backend=='openai' else 'gpt-6.1-sol'),
                   messages=[{'role':'user','content':'test'}], **kw)


class API:
    def __init__(self, message, delay=0):
        self.message,self.delay=message,delay
        self.chat=NS(completions=self)
    async def create(self, **kwargs):
        self.kwargs=kwargs
        await asyncio.sleep(self.delay)
        return NS(choices=[NS(message=self.message)], usage=None)


@pytest.mark.parametrize('kind',['final','tools'])
async def test_common_contract(kind):
    final = kind=='final'
    expected=Reply(kind=kind,text='답변' if final else '',tools=[] if final else [{'name':'get_workspace','arguments':{}}])
    api=API(NS(content='답변' if final else None,tool_calls=[] if final else [NS(function=NS(name='get_workspace',arguments='{}'))]))
    assert await OpenAIAdapter('test',api).complete(request()) == expected
    events=[{'type':'thread.started','thread_id':'fresh'}, {'type':'item.completed','item':{'type':'agent_message','text':expected.model_dump_json()}}]
    assert CodexCLIAdapter.parse_events('\n'.join(json.dumps(e) for e in events).encode())==expected


@pytest.mark.parametrize('payload',[b'not json',b'{"type":"turn.completed"}',b'{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}'])
def test_cli_bad_output(payload):
    with pytest.raises(ModelError): CodexCLIAdapter.parse_events(payload)


async def test_missing_key_never_switches():
    with pytest.raises(ModelError,match='api_key_missing'):
        await OpenAIAdapter('').complete(request())


async def test_cli_fail_closed():
    with pytest.raises(ModelError,match='cli_isolation_unverified'):
        await CodexCLIAdapter('codex','untrusted/path').complete(request('codex_cli'))


async def test_api_timeout_and_cancel():
    adapter=OpenAIAdapter('test',API(NS(),delay=5))
    with pytest.raises(ModelError,match='model_timeout'):
        await adapter.complete(request(timeout=.01))
    task=asyncio.create_task(adapter.complete(request()))
    await asyncio.sleep(.01);task.cancel()
    with pytest.raises(asyncio.CancelledError): await task


async def test_api_invalid_tool_json():
    api=API(NS(content=None,tool_calls=[NS(function=NS(name='get_workspace',arguments='not-json'))]))
    with pytest.raises(ModelError,match='invalid_model_output'):
        await OpenAIAdapter('test',api).complete(request())


async def test_api_preserves_over_budget_proposals_for_graph_rejection():
    api=API(NS(content=None,tool_calls=[NS(function=NS(name='get_workspace',arguments='{}'))]*4))
    reply=await OpenAIAdapter('test',api).complete(request())
    assert reply.kind=='tools' and len(reply.tools)==4


async def test_api_reasoning_model_uses_completion_token_parameter():
    api=API(NS(content='synthetic',tool_calls=[]))
    req=request();req.binding=Binding(backend='openai',model='gpt-5.4-mini-2026-03-17')
    await OpenAIAdapter('test',api).complete(req)
    assert api.kwargs['max_completion_tokens']==req.max_output_tokens
    assert 'max_tokens' not in api.kwargs


@pytest.mark.parametrize('status,code,expected,retryable',[
    (400,'invalid_request_error','api_request_rejected',False),
    (403,'model_not_found','api_model_unavailable',False),
    (403,'permission_denied','api_permission_denied',False),
    (404,'model_not_found','api_model_unavailable',False),
    (500,'server_error','api_transient_error',True),
])
async def test_api_status_failures_are_sanitized_and_not_retried_as_configuration(status,code,expected,retryable):
    import httpx
    from openai import APIStatusError
    class Rejected(API):
        async def create(self,**kwargs):
            raise APIStatusError('private diagnostic must not escape',response=httpx.Response(status,
                request=httpx.Request('POST','https://api.openai.com/v1/chat/completions')),body={'code':code})
    with pytest.raises(ModelError,match=expected) as failure:
        await OpenAIAdapter('synthetic',Rejected(None)).complete(request())
    assert failure.value.retryable==retryable
    assert 'private diagnostic' not in str(failure.value)


@pytest.mark.parametrize('code',['insufficient_quota','billing_hard_limit_reached','credit_balance_exhausted',
    'organization_spend_limit_exceeded','project_spend_limit_exceeded','organization_usage_limit_exceeded'])
async def test_billing_limit_is_not_treated_as_transient_rate_limit(code):
    import httpx
    from openai import RateLimitError
    class Quota(API):
        async def create(self,**kwargs):
            raise RateLimitError('synthetic',response=httpx.Response(429,
                request=httpx.Request('POST','https://api.openai.com/v1/chat/completions')),body={'code':code})
    with pytest.raises(ModelError,match='api_quota_exhausted') as failure:
        await OpenAIAdapter('synthetic',Quota(None)).complete(request())
    assert failure.value.retryable is False


@pytest.mark.parametrize('name,args',[('update_part',{}),('shell',{}),('get_workspace',{'session_id':'other'}),('search_documents',{'query':''})])
def test_harness_denies(name,args):
    from fastapi import HTTPException
    with pytest.raises(HTTPException): validate_tool('chat',name,args)


@pytest.mark.parametrize('env',[{'llm_backend':'automatic'},{'openai_model':'other'}])
def test_configuration_errors(env):
    with pytest.raises(ValidationError):
        Settings(_env_file=None,jwt_secret='x'*32,internal_secret='y'*32,**env)


def test_cli_native_tool_is_not_a_proposal():
    with pytest.raises(ModelError,match='boundary_violation'):
        CodexCLIAdapter.parse_events(b'{"type":"item.completed","item":{"type":"command_execution"}}')


async def test_cli_primitive_process_timeout_and_cancel(tmp_path):
    # Exercise the real subprocess primitive with a harmless test executable,
    # never the product CLI without isolation.
    binary=tmp_path/'slow'
    binary.write_text('#!/bin/sh\nexec sleep 30\n');binary.chmod(0o700)
    adapter=CodexCLIAdapter(str(binary),'')
    with pytest.raises(ModelError,match='model_timeout'):
        await adapter._execute_verified(request('codex_cli',timeout=.05),[],str(tmp_path))
    task=asyncio.create_task(adapter._execute_verified(request('codex_cli'),[],str(tmp_path)))
    await asyncio.sleep(.05);task.cancel()
    with pytest.raises(asyncio.CancelledError): await task


async def test_cli_child_environment_and_jsonl(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','must-not-leak')
    monkeypatch.setenv('CODEX_API_KEY','must-not-leak')
    binary=tmp_path/'fake-cli'
    binary.write_text('''#!/usr/bin/env python3
import json,os,sys
assert not os.getenv('OPENAI_API_KEY') and not os.getenv('CODEX_API_KEY')
assert 'resume' not in sys.argv
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'kind':'final','text':'ok','tools':[]})}}))
''');binary.chmod(0o700)
    result=await CodexCLIAdapter(str(binary),'')._execute_verified(request('codex_cli'),[],str(tmp_path))
    assert result.text=='ok'


async def test_closed_job_cancels_inflight_handler(monkeypatch):
    from dayzero.workers.runtime import run_job
    import httpx
    stopped=asyncio.Event()
    async def handler(job,internal):
        try: await asyncio.Future()
        finally: stopped.set()
    async def lost_lease(internal):
        await asyncio.sleep(.01)
        response=httpx.Response(409,request=httpx.Request('POST','http://backend/heartbeat'))
        raise httpx.HTTPStatusError('closed',request=response.request,response=response)
    monkeypatch.setattr('dayzero.workers.runtime.heartbeat',lost_lease)
    with pytest.raises(httpx.HTTPStatusError):await run_job({},None,handler)
    assert stopped.is_set()


def test_company_information_requests_require_real_sources():
    from dayzero.agents.harness import requires_sources
    assert requires_sources('운영 정책 문서를 찾아 요약해 주세요.')
    assert not requires_sources('인터뷰 자료가 늦어서 먼저 문제 정의를 쓰겠습니다.')
    assert not requires_sources('도와주셔서 감사합니다.')


async def test_frozen_binding_overrides_new_environment_without_fallback():
    from dayzero.llm.client import complete
    cli_config=Settings(_env_file=None,jwt_secret='x'*32,internal_secret='y'*32,llm_backend='codex_cli')
    with pytest.raises(ModelError,match='api_key_missing'):
        await complete(request('openai'),cli_config)
    api_config=Settings(_env_file=None,jwt_secret='x'*32,internal_secret='y'*32,llm_backend='openai',openai_api_key='synthetic')
    with pytest.raises(ModelError,match='cli_isolation_unverified'):
        await complete(request('codex_cli'),api_config)


def test_report_binding_is_independent_and_defaults_to_agent(monkeypatch):
    from dayzero.llm.client import bind
    cfg=Settings(_env_file=None,jwt_secret='x'*32,internal_secret='y'*32)
    assert bind(cfg,report=True)==bind(cfg)
    cfg=cfg.model_copy(update={'report_llm_backend':'codex_cli','report_model':'gpt-5.6-sol'})
    monkeypatch.setattr('dayzero.llm.client.subprocess.run',lambda *a,**kw:NS(returncode=0,stdout='codex-cli 0.153.4\n'))
    assert bind(cfg).backend=='openai' and bind(cfg).model=='gpt-4o-mini'
    report=bind(cfg,report=True)
    assert report.backend=='codex_cli' and report.model=='gpt-5.6-sol' and report.cli_version=='codex-cli 0.153.4'


async def test_rate_limit_retries_share_budget_and_keep_same_adapter(monkeypatch):
    from dayzero.llm.client import complete
    from unittest.mock import AsyncMock
    count=0
    class Limited:
        usage=None
        async def complete(self,req):
            nonlocal count
            count+=1
            if count<3:raise ModelError('cli_rate_limited',True,retry_after=30)
            return Reply(kind='final',text='같은 경로에서 재시도 성공',tools=[])
    monkeypatch.setattr('dayzero.llm.client.adapter',lambda binding,config:Limited())
    sleep=AsyncMock();reserve=AsyncMock()
    monkeypatch.setattr('dayzero.llm.client.asyncio.sleep',sleep)
    cfg=Settings(_env_file=None,jwt_secret='x'*32,internal_secret='y'*32)
    result,meta=await complete(request('codex_cli'),cfg,reserve=reserve)
    assert result.kind=='final' and count==3 and reserve.await_count==3
    assert 30<=sleep.await_args_list[0].args[0]<30.5
    assert 60<=sleep.await_args_list[1].args[0]<60.5
    assert meta['tokens'] is None and meta['cost'] is None
