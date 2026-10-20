from urllib.parse import urlsplit
from mcp.server.fastmcp import FastMCP
from dayzero.shared.internal import Internal
from dayzero.shared.config import settings
import httpx

mcp = FastMCP('Day Zero Business', host=settings().mcp_host, port=urlsplit(settings().business_mcp_url).port or 8001)


@mcp.tool()
async def business_call(job_id: str, job_token: str, name: str, arguments: dict, operation_id: str, context_event_version: int | None = None) -> dict:
    """Execute a backend-authorized, job-scoped business proposal."""
    try:
        return await Internal(job_id, job_token).call('business', 'POST',
            {'name': name, 'arguments': arguments, 'operation_id': operation_id, 'context_event_version': context_event_version})
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 409 and error.response.json().get('detail') == 'version_conflict':
            return {'error': 'version_conflict',
                    'instruction': 'get_workspace로 현재 버전을 확인하고 그 버전으로 다시 제안하세요. 버전 증가는 Backend가 처리합니다.'}
        if error.response.status_code in [404, 422] or (error.response.status_code == 409 and error.response.json().get('detail') not in ['event_context_changed', 'stale_lease', 'session_closed']):
            return {'error': error.response.json().get('detail', 'tool_rejected'),
                    'instruction': '권한·합의 근거와 인자 스키마를 확인하세요. 실행되지 않았습니다.'}
        raise


if __name__ == '__main__':
    mcp.run(transport='streamable-http')
