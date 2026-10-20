from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ADAPTER_VERSION = '1.0.2'


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    backend: Literal['openai', 'codex_cli']
    model: str
    adapter_version: str = ADAPTER_VERSION
    cli_version: str | None = None


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    arguments: dict[str, Any]


class Reply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['final', 'tools']
    text: str
    # Proposals are untrusted input. The Graph rejects an over-budget batch
    # before execution and can request a bounded correction.
    tools: list[Proposal]

    @model_validator(mode='after')
    def shape(self):
        if self.kind == 'tools' and not self.tools:
            raise ValueError('tool response needs proposals')
        if self.kind == 'final' and (self.tools or not self.text.strip()):
            raise ValueError('final response needs text and no proposals')
        return self


class Request(BaseModel):
    binding: Binding
    messages: list[dict[str, str]]
    tools: list[dict] = Field(default_factory=list)
    timeout: float = 45
    max_output_tokens: int = 1800

    @model_validator(mode='after')
    def input_budget(self):
        if sum(len(m.get('content', '')) for m in self.messages) > 80000:
            raise ValueError('model_input_budget_exceeded')
        return self


class ModelError(Exception):
    def __init__(self, code: str, retryable: bool = False, retry_after: float = 0):
        self.code, self.retryable, self.retry_after = code, retryable, retry_after
        super().__init__(code)


class WireProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    arguments_json: str


class CLIReply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['final', 'tools']
    text: str
    tools: list[WireProposal]
