from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


Owner = Literal['candidate', 'colleague', 'shared']
Status = Literal['todo', 'doing', 'waiting', 'done', 'cancelled']


class TaskCreate(Strict):
    title: str = Field(min_length=1, max_length=300)
    owner: Owner
    part_id: str | None = None
    source_id: str | None = None
    note: str = Field(default='', max_length=4000)
    status: Literal['todo', 'waiting'] = 'todo'
    due_elapsed: float | None = Field(default=None, ge=0, le=7200)
    delay_seconds: float | None = Field(default=None, gt=0, le=7200,
        description='현재부터 기다릴 초. 지정하면 waiting으로 생성하고 Backend가 약속 시점을 계산합니다. 10분은 600초입니다.')


class TaskChange(Strict):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    owner: Owner | None = None
    status: Status | None = None
    note: str | None = Field(default=None, max_length=4000)
    source_id: str | None = None
    due_elapsed: float | None = Field(default=None, ge=0, le=7200)


class CreateTask(TaskCreate):
    source_id: str = Field(min_length=1)


class UpdateTask(Strict):
    task_id: str
    change: TaskChange


class CompleteTask(Strict):
    task_id: str
    version: int = Field(ge=1)
    part_version: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=20000)
    note: str = Field(max_length=4000)


class Remember(Strict):
    source_id: str
    note: str = Field(min_length=1, max_length=2000)
