"""Versioned text-work scenarios. A session pins the complete definition at invitation."""
import json
import os
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(os.getenv('SCENARIO_ROOT', Path(__file__).resolve().parents[5] / 'scenarios'))


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Person(Strict):
    name: str
    role: str
    introduction: str


class Section(Strict):
    title: str
    owner: Literal['candidate', 'colleague', 'shared'] = 'shared'


class Document(Strict):
    slug: str
    title: str
    body: str
    release_after: int = Field(ge=0, lt=7200)


class Situation(Strict):
    title: str
    text: str


class Event(Strict):
    key: str
    offset: int = Field(ge=0, lt=7200)
    actor: Literal['leader', 'colleague', 'system']
    text: str
    review_body: str | None = None
    review_part: int | None = None
    release_slug: str | None = None
    debrief: Situation | None = None


class Scenario(Strict):
    id: str = Field(pattern=r'^[a-z][a-z0-9-]+$')
    title: str
    company: str
    team: str
    service: str
    introduction: str
    brief: str
    short_brief: str
    deliverable_title: str
    deliverable_description: str
    completion_conditions: list[str] = Field(min_length=1)
    roles: dict[Literal['leader', 'colleague'], Person]
    sections: list[Section] = Field(min_length=1, max_length=12)
    documents: list[Document]
    events: list[Event]
    initial_target: str
    changed_target: str
    duration_seconds: Literal[7200] = 7200
    workflow: Literal['legacy', 'conversation'] = 'conversation'

    @model_validator(mode='after')
    def validate_references(self):
        if set(self.roles) != {'leader', 'colleague'}:
            raise ValueError('leader and colleague are required')
        if len({e.key for e in self.events}) != len(self.events):
            raise ValueError('event keys must be unique')
        if len({d.slug for d in self.documents}) != len(self.documents):
            raise ValueError('document slugs must be unique')
        for event in self.events:
            if event.review_body is not None and not (event.review_part and 1 <= event.review_part <= len(self.sections)):
                raise ValueError('review must reference an existing section')
            if event.release_slug and not any(d.slug == event.release_slug and d.release_after == event.offset for d in self.documents):
                raise ValueError('document release must match its event')
        return self


def load_scenario(scenario_id):
    if not re.fullmatch(r'[a-z][a-z0-9-]+', scenario_id):
        raise ValueError('invalid_scenario')
    folder = ROOT / scenario_id
    try:
        value = json.loads((folder / 'scenario.json').read_text())
        files = value['documents']
        if any(Path(name).name != name or not name.endswith('.json') for name in files):
            raise ValueError('invalid_document_path')
        value['documents'] = [json.loads((folder / 'documents' / name).read_text()) for name in files]
        definition = Scenario.model_validate(value)
        if definition.id != scenario_id:
            raise ValueError('scenario_id_mismatch')
        return definition.model_dump()
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError('invalid_scenario') from error


def session_scenario(session):
    pinned = (session.contract or {}).get('scenario_definition')
    return pinned if pinned is not None else load_scenario(session.scenario_version)


def public_scenario(definition, elapsed=0):
    # Future event text, review drafts and unreleased source bodies stay server-side.
    keys = ['id', 'title', 'company', 'team', 'service', 'introduction', 'brief', 'short_brief',
            'deliverable_title', 'deliverable_description', 'completion_conditions', 'roles',
            'sections', 'duration_seconds', 'workflow', 'initial_target']
    return {**{key: definition[key] for key in keys},
            'documents': [{'title': d['title'], 'available': d['release_after'] <= elapsed}
                          for d in definition['documents']]}


def public_contract(contract):
    return {key: value for key, value in (contract or {}).items() if key != 'scenario_definition'}


def document_slug(definition, slug):
    return slug if definition['id'] == 'looplab-v1' else definition['id']+'--'+slug


def available_scenarios():
    # Legacy invitations remain supported, but new invitations use the conversational flow.
    return [public_scenario(load_scenario(path.parent.name)) for path in sorted(ROOT.glob('*/scenario.json'))
            if path.parent.name != 'looplab-v1']
