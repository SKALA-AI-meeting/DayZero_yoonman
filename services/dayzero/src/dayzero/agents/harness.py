import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from fastapi import HTTPException
from dayzero.llm.schemas import ModelError
from dayzero.contracts.work import CreateTask, UpdateTask, CompleteTask, Remember


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Empty(Strict):
    pass


class Search(Strict):
    query: str = Field(min_length=1, max_length=500)


class Document(Strict):
    document_id: str = Field(min_length=1, max_length=200)


class Change(Strict):
    version: int = Field(ge=1)
    body: str | None = Field(default=None, max_length=20000)
    status: Literal['todo', 'doing', 'done'] | None = None


class Update(Strict):
    part_id: str
    change: Change


class Owners(Strict):
    owners: dict[str, Literal['candidate', 'colleague', 'shared']]
    accepted: bool
    source_id: str | None = None


WRITES = {'update_part', 'negotiate', 'create_task', 'update_task', 'complete_task'}
AGENTS = {'leader', 'colleague', 'chat'}

TOOLS = {'create_task': ('business', CreateTask, '지원자 요청·합의 메시지 source_id로 태스크 생성. 나중에 할 일은 같은 태스크에 delay_seconds=대기 초(10분=600), 담당 owner와 part_id를 함께 지정. Backend가 waiting과 약속 시점을 저장'),
         'update_task': ('business', UpdateTask, '담당 태스크 변경. 거절은 cancelled, 보류는 waiting과 due_elapsed 초로 약속 기록'),
         'complete_task': ('business', CompleteTask, '담당 태스크의 문서와 완료 상태를 동시에 저장. 전체 파트 본문과 최신 버전 필요'),
         'remember': ('business', Remember, '본인에게 공개된 source_id에 연결해 결정·보고·질문·약속 기억'),
         'get_workspace': ('business', Empty, '현재 태스크·담당·문서 확인'),
         'get_history': ('business', Empty, '기획안 버전·작성 주체 이력 확인'),
         'update_part': ('business', Update, '동료 담당 또는 공동 파트 변경'),
         'negotiate': ('business', Owners, '지원자와 분업 협의, 최대 3회'),
         'search_documents': ('rag', Search, '공개된 회사 문서를 검색하고 원문·출처 확인'),
         'get_document': ('rag', Document, '현재 공개된 회사 문서 원문 확인')}


def tool_definitions(agent_id, context=None):
    definitions = []
    for name, (_, schema, desc) in TOOLS.items():
        if agent_id not in AGENTS or (agent_id != 'colleague' and name in WRITES):
            continue
        parameters = schema.model_json_schema()
        if context:
            if name == 'negotiate':
                numbers = [str(p['number']) for p in context['parts']]
                parameters['properties']['owners'] = {'type': 'object',
                    'properties': {n: {'type':'string','enum':['candidate','colleague','shared']} for n in numbers},
                    'required': numbers, 'additionalProperties': False}
            targets = None
            field = None
            if name == 'update_part':
                field, targets = 'part_id', [p['id'] for p in context['parts'] if p['owner'] in ['colleague', 'shared']]
            elif name in ['update_task', 'complete_task']:
                field, targets = 'task_id', [t['id'] for t in context['tasks'] if t['owner'] in ['colleague', 'shared'] and
                    (name!='complete_task' or t['status'] not in ['done', 'cancelled'])]
            elif name == 'remember':
                field, targets = 'source_id', [r['id'] for r in context['history']]
            elif name == 'create_task':
                field, targets = 'source_id', [r['id'] for r in context['history'] if r['actor']=='candidate' and r['data'].get('flow')=='collaboration']
            if field:
                if not targets:
                    continue
                parameters['properties'][field] = {'type': 'string', 'enum': targets}
        definitions.append({'type': 'function', 'function': {'name': name, 'description': desc,
                            'parameters': parameters}})
    return definitions


def validate_tool(agent_id, name, arguments, part_numbers=None):
    if agent_id not in AGENTS or name not in TOOLS or (agent_id != 'colleague' and name in WRITES):
        raise HTTPException(403, 'tool_not_allowed')
    try:
        result = TOOLS[name][1].model_validate(arguments).model_dump()
        if name == 'negotiate' and set(result['owners']) != {str(n) for n in (part_numbers if part_numbers is not None else range(1,6))}:
            raise ValueError('all scenario sections require an owner')
        return result
    except (ValidationError, ValueError):
        raise HTTPException(422, 'invalid_tool_arguments') from None


FORBIDDEN = re.compile(r'(합격\s*(가능|확률|입니다)|불합격입니다|점수는?\s*\d|인성[이은].{0,8}(좋|나쁘)|00:30.*도움|01:10.*바꿔)')


def guard_text(text):
    if not text.strip() or len(text) > 14000 or FORBIDDEN.search(text):
        raise ModelError('output_guard_rejected')
    return text


def evaluation_question(text):
    return bool(re.search(r'평가\s*기준|합격\s*가능|점수|연출.{0,8}상황|앞으로.{0,8}이벤트', text))


def bounded_result(value):
    result = json.dumps(value, ensure_ascii=False)
    if len(result) > 16000:
        return result[:16000] + '\n[도구 결과 길이 제한]'
    return result


def requires_sources(text):
    topics = r'회사|서비스|모아런|루프랩|정책|지표|인터뷰|문서|자료'
    query = r'알려|확인해|정리해|요약해|찾아|검색|[?？]'
    return bool(re.search(topics, text) and re.search(query, text))
