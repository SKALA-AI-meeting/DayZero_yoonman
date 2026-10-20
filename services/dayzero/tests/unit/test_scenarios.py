import copy
import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from dayzero.shared.scenarios import Scenario, load_scenario, public_scenario, session_scenario
from dayzero.agents.harness import tool_definitions, validate_tool


def test_public_definition_excludes_future_situations_and_unreleased_bodies():
    definition = load_scenario('teamnote-onboarding-v1')
    public = public_scenario(definition)
    encoded = json.dumps(public, ensure_ascii=False)
    assert definition['changed_target'] not in encoded
    for event in definition['events']:
        assert event['text'] not in encoded
    assert all(document['body'] not in encoded for document in definition['documents'])
    assert next(d for d in public['documents'] if d['title']=='고객 인터뷰 요약')['available'] is False
    assert all(d['available'] for d in public_scenario(definition, 3900)['documents'])


def test_pinned_session_definition_survives_a_changed_or_missing_catalog(monkeypatch):
    pinned = load_scenario('looplab-v2')
    session = SimpleNamespace(contract={'scenario_definition': pinned}, scenario_version=pinned['id'])
    monkeypatch.setattr('dayzero.shared.scenarios.load_scenario', lambda _: pytest.fail('must use the invitation snapshot'))
    assert session_scenario(session) == pinned


def test_three_section_division_matches_actual_sections_not_five_fixed_parts():
    definition = load_scenario('teamnote-onboarding-v1')
    context = {'parts': [{'id':str(i),'number':i,'owner':'shared'} for i in range(1,4)], 'tasks':[], 'history':[]}
    schema = next(t['function']['parameters'] for t in tool_definitions('colleague', context) if t['function']['name']=='negotiate')
    assert schema['properties']['owners']['required'] == ['1','2','3']
    arguments = {'owners':{'1':'candidate','2':'colleague','3':'shared'}, 'accepted':True}
    assert validate_tool('colleague','negotiate',arguments,part_numbers=[1,2,3])['owners']==arguments['owners']
    assert len(definition['sections'])==3


@pytest.mark.parametrize('change', ['duplicate_event','missing_release','invalid_review_section'])
def test_invalid_scenario_references_are_rejected_before_invitation(change):
    definition = copy.deepcopy(load_scenario('teamnote-onboarding-v1'))
    if change=='duplicate_event':
        definition['events'].append(definition['events'][0])
    elif change=='missing_release':
        next(e for e in definition['events'] if e['key']=='release')['release_slug']='missing'
    else:
        next(e for e in definition['events'] if e['key']=='review')['review_part']=4
    with pytest.raises(ValidationError):
        Scenario.model_validate(definition)


def test_scenario_path_cannot_escape_catalog():
    with pytest.raises(ValueError):
        load_scenario('../looplab-v1')
