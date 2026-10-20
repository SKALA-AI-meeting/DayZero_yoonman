import asyncio
import json
import hashlib
from dayzero.shared.internal import Internal
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from dayzero.shared.config import settings
from dayzero.agents.harness import TOOLS, validate_tool
from dayzero.llm.schemas import ModelError


async def execute(flow, job_id, job_token, proposal, operation_id, event_version):
    fingerprint = hashlib.sha256(json.dumps(proposal.model_dump(), sort_keys=True).encode()).hexdigest()
    await Internal(job_id, job_token).call('tool-attempt', 'POST',
        {'operation_id': operation_id, 'digest': fingerprint})
    args = validate_tool(flow, proposal.name, proposal.arguments)
    server = TOOLS[proposal.name][0]
    url = settings().business_mcp_url if server == 'business' else settings().rag_mcp_url
    body = {'job_id': job_id, 'job_token': job_token, 'name': proposal.name, 'arguments': args}
    if server == 'business':
        body['operation_id'] = operation_id
        body['context_event_version'] = event_version
    try:
        async with asyncio.timeout(20):
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    result = await client.call_tool('business_call' if server == 'business' else 'rag_call', body)
        # Classify after transport cleanup so task groups do not wrap domain errors.
        if result.isError:
            safe_text = ''.join(c.text for c in result.content if c.type == 'text')
            if 'event_context_changed' in safe_text:
                raise ModelError('event_context_changed', retryable=True)
            raise ModelError('mcp_tool_failed', retryable=True)
        if result.structuredContent:
            return result.structuredContent
        return json.loads(''.join(c.text for c in result.content if c.type == 'text'))
    except TimeoutError:
        raise ModelError('mcp_timeout', retryable=True) from None
