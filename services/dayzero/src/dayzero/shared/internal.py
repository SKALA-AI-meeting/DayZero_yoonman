import httpx
from dayzero.shared.config import settings


class Internal:
    def __init__(self, job_id: str, token: str):
        self.job_id, self.token = job_id, token

    async def call(self, suffix, method='GET', body=None):
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method,
                f'{settings().backend_url}/internal/jobs/{self.job_id}/{suffix}',
                headers={'Authorization': 'Bearer '+self.token}, json=body)
            if response.status_code == 409 and response.json().get('detail') == 'event_context_changed':
                from dayzero.llm.schemas import ModelError
                raise ModelError('event_context_changed', retryable=True)
            if response.status_code == 429 and response.json().get('detail') in ['model_budget_exhausted', 'tool_budget_exhausted']:
                from dayzero.llm.schemas import ModelError
                raise ModelError(response.json()['detail'])
            response.raise_for_status()
            return response.json()
