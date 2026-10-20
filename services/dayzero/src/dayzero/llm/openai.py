import asyncio
import json
from openai import AsyncOpenAI, AuthenticationError, RateLimitError, APIStatusError, APIConnectionError, APITimeoutError
from pydantic import ValidationError
from dayzero.llm.schemas import Request, Reply, ModelError


class OpenAIAdapter:
    def __init__(self, key: str, client=None):
        self.key = key
        self.client = client
        self.usage = None

    async def complete(self, request: Request) -> Reply:
        if not self.key:
            raise ModelError('api_key_missing')
        owned = self.client is None
        client = self.client or AsyncOpenAI(api_key=self.key, max_retries=0)
        try:
            async with asyncio.timeout(request.timeout):
                kwargs = {'model': request.binding.model, 'messages': request.messages,
                          'max_completion_tokens': request.max_output_tokens}
                if request.tools:
                    kwargs['tools'] = request.tools
                response = await client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            self.usage = response.usage.model_dump() if response.usage else None
            if msg.tool_calls:
                return Reply(kind='tools', text='', tools=[
                    {'name': t.function.name, 'arguments': json.loads(t.function.arguments)}
                    for t in msg.tool_calls
                ])
            return Reply(kind='final', text=msg.content or '', tools=[])
        except AuthenticationError:
            raise ModelError('api_authentication_failed') from None
        except RateLimitError as error:
            if error.code in ['insufficient_quota', 'billing_hard_limit_reached', 'credit_balance_exhausted',
                    'organization_spend_limit_exceeded', 'project_spend_limit_exceeded', 'organization_usage_limit_exceeded']:
                raise ModelError('api_quota_exhausted') from None
            try:
                retry_after = min(60, max(1, float(error.response.headers.get('retry-after', '30'))))
            except ValueError:
                retry_after = 30
            raise ModelError('api_rate_limited', retryable=True, retry_after=retry_after) from None
        except (APIConnectionError, APITimeoutError):
            raise ModelError('api_transient_error', retryable=True) from None
        except APIStatusError as error:
            if error.code == 'model_not_found':
                raise ModelError('api_model_unavailable') from None
            if error.status_code == 403:
                raise ModelError('api_permission_denied') from None
            if error.status_code == 400:
                raise ModelError('api_request_rejected') from None
            raise ModelError('api_transient_error' if error.status_code >= 500 else 'api_request_failed',
                retryable=error.status_code >= 500) from None
        except TimeoutError:
            raise ModelError('model_timeout', retryable=True) from None
        except (ValidationError, ValueError, IndexError):
            raise ModelError('invalid_model_output') from None
        finally:
            if owned:
                await client.close()
