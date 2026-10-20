import asyncio
import os
import subprocess
import time
import random
from dayzero.llm.schemas import Binding, Request, ModelError, ADAPTER_VERSION
from dayzero.llm.openai import OpenAIAdapter
from dayzero.llm.codex_cli import CodexCLIAdapter
from dayzero.shared.config import Settings


def bind(config: Settings, *, report=False) -> Binding:
    backend = (config.report_llm_backend or config.llm_backend) if report else config.llm_backend
    model = config.report_model if report else None
    if backend == 'openai':
        return Binding(backend='openai', model=model or config.openai_model)
    try:
        proc = subprocess.run([config.codex_bin, '--version'], capture_output=True, text=True, timeout=5,
            env={k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'LANG', 'CODEX_HOME'}})
    except (OSError, subprocess.TimeoutExpired):
        raise ModelError('cli_unavailable') from None
    if proc.returncode:
        raise ModelError('cli_unavailable')
    return Binding(backend='codex_cli', model=model or config.codex_model, cli_version=proc.stdout.strip())


def adapter(binding: Binding, config: Settings):
    if binding.adapter_version != ADAPTER_VERSION:
        raise ModelError('adapter_version_unavailable')
    if binding.backend == 'openai':
        return OpenAIAdapter(config.openai_api_key.get_secret_value())
    return CodexCLIAdapter(config.codex_bin, config.codex_isolation_profile)


async def complete(request: Request, config: Settings, reserve=None):
    model = adapter(request.binding, config)
    started = time.monotonic()
    for attempt in range(3):
        try:
            if reserve:
                await reserve()
            result = await model.complete(request)
            if not request.tools and result.kind == 'tools':
                raise ModelError('tools_not_allowed')
            raw = model.usage or {}
            if request.binding.backend == 'openai':
                tokens = None if model.usage is None else {
                    'input_tokens': raw.get('prompt_tokens'), 'output_tokens': raw.get('completion_tokens'),
                    'cached_input_tokens': (raw.get('prompt_tokens_details') or {}).get('cached_tokens'),
                    'reasoning_output_tokens': (raw.get('completion_tokens_details') or {}).get('reasoning_tokens')}
            else:
                tokens = None if model.usage is None else {k: raw.get(k) for k in [
                    'input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens']}
            return result, {'backend': request.binding.backend, 'model': request.binding.model,
                'attempts': attempt+1, 'latency_ms': round((time.monotonic()-started)*1000),
                'tokens': tokens, 'cost': None}
        except ModelError as error:
            if not error.retryable or attempt == 2:
                error.retryable = False  # no additional queue retry for an exhausted model retry budget
                raise
            await asyncio.sleep((error.retry_after or 1) * (2 ** attempt) + random.uniform(0, 0.5))
