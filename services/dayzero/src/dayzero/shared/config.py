from functools import lru_cache
from typing import Literal
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    llm_backend: Literal['openai', 'codex_cli'] = 'openai'
    report_llm_backend: Literal['openai', 'codex_cli'] | None = None
    report_model: str | None = None
    openai_api_key: SecretStr = SecretStr('')
    openai_model: str = 'gpt-4o-mini'
    codex_bin: str = 'codex'
    codex_model: str = 'gpt-6.1-sol'
    codex_isolation_profile: str = ''
    database_url: str = 'postgresql+psycopg://dayzero:dayzero@localhost:55432/dayzero'
    checkpoint_database_url: str = 'postgresql://graph:graph@localhost:55432/dayzero'
    rag_database_url: str = 'postgresql://rag:rag@localhost:55432/dayzero'
    rabbitmq_url: str = 'amqp://dayzero:dayzero@localhost:5673/'
    jwt_secret: SecretStr
    internal_secret: SecretStr
    backend_url: str = 'http://localhost:8000'
    mcp_host: str = '127.0.0.1'
    business_mcp_url: str = 'http://localhost:8001/mcp'
    rag_mcp_url: str = 'http://localhost:8002/mcp'
    web_origin: str = 'http://localhost:3000'

    @model_validator(mode='after')
    def validate_secrets(self):
        if min(len(self.jwt_secret.get_secret_value()), len(self.internal_secret.get_secret_value())) < 32:
            raise ValueError('JWT_SECRET and INTERNAL_SECRET must each contain at least 32 characters')
        if self.openai_model != 'gpt-4o-mini':
            raise ValueError('The API evaluation path requires gpt-4o-mini')
        return self


@lru_cache
def settings():
    return Settings()
