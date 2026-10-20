"""Compute provenance and windows in code; LLM may select and phrase supported facts.

An exact quotation check proves provenance, not semantic accuracy. Reports expose
originals and limitations for human review; they never assert a competency level.
"""
import json
from pydantic import BaseModel, ConfigDict, Field
from dayzero.llm.schemas import Binding, Request, ModelError
from dayzero.llm.client import complete
from dayzero.shared.config import settings

ITEMS = [('업무 수행 역량', '요구사항 반영', None), ('업무 수행 역량', '근거 활용', None),
         ('업무 수행 역량', '실행 가능성', None), ('AI 활용 역량', '위임 판단', None),
         ('AI 활용 역량', '맥락 전달', None), ('AI 활용 역량', '결과 검증', None),
         ('협업 태도', '책임감', 'delay'), ('협업 태도', '존중·소통', 'review'),
         ('협업 태도', '협력 태도', 'help'), ('협업 태도', '유연성', 'change')]


def evidence_catalog(snapshot):
    catalog = {}
    for r in snapshot['records']:
        if r['kind'] not in ['message', 'part', 'division', 'task', 'review_draft']:
            continue
        data = r['data']
        quote = data['text'] if r['kind']=='message' else json.dumps(data, ensure_ascii=False, sort_keys=True)
        catalog[r['id']] = {'id': r['id'], 'kind': r['kind'], 'actor': r['actor'], 'at': r['at'],
            'elapsed': r['elapsed'], 'quote': quote, 'data': data}
    for d in snapshot.get('documents', []):
        catalog[d['id']] = {'id': d['id'], 'kind': 'company_document', 'actor': 'company',
            'at': None, 'elapsed': d['release_after'], 'quote': d['body'],
            'data': {'title': d['title'], 'version': d['version']}}
    if snapshot.get('version', 1) >= 2:
        for part in snapshot['parts']:
            changes = [r for r in snapshot['records'] if r['kind']=='part' and r['data']['after']['id']==part['id']]
            last = changes[-1] if changes else None
            eid = 'final:'+part['id']+':v'+str(part['version'])
            catalog[eid] = {'id': eid, 'kind': 'final_part', 'actor': last['actor'] if last else 'unknown',
                'at': last['at'] if last else snapshot['ended'],
                'elapsed': last['elapsed'] if last else snapshot['ended']-snapshot['started'],
                'quote': part['body'], 'data': part}
    return catalog


def baseline(snapshot):
    events = {e['key']: e for e in snapshot['events']}
    uses_ai = any(r['actor']=='candidate' and r['kind']=='message' and
                  r['data'].get('flow')=='chat' for r in snapshot['records'])
    rows = []
    for axis, item, key in ITEMS:
        event = events.get(key)
        opportunity = not key or bool(event and event['received_at'])
        since = (event['received_at'] - snapshot['started']) if key and opportunity else 0
        status = ('사용하지 않음' if axis=='AI 활용 역량' and not uses_ai else
                  '기록 불충분' if key and event and event.get('stored_at') and not event.get('received_at') else
                  '관찰 기회 없음' if not opportunity else '기록 불충분' if any(g['flow']=='chat' for g in snapshot.get('record_gaps', [])) and axis=='AI 활용 역량'
                  else '관련 행동이 기록되지 않음')
        rows.append({'axis': axis, 'item': item, 'situation': key or '세션 과제 수행',
            'window': [since, snapshot['ended']-snapshot['started']], 'status': status,
            'observation': '', 'evidence': [],
            'limitation': '원문에 나타난 행동만 제공합니다. 수행 수준·인성·검증 성공 여부는 사람이 판단합니다.'})
    return rows


def utilization_links(snapshot):
    """Link explicit use and exact phrase overlap without inferring copying/verification."""
    catalog = evidence_catalog(snapshot)
    answers = {r['id']: r for r in snapshot['records'] if r['kind']=='message' and r['actor']=='assistant'}
    links = []
    for record in snapshot['records']:
        if record['actor'] != 'candidate' or record['kind'] not in ['message', 'part']:
            continue
        data = record['data']
        text = data.get('text', '') if record['kind']=='message' else data['after']['body']
        for aid, answer in answers.items():
            if record['at'] < answer['at']:
                continue
            original = answer['data']['text']
            explicit = data.get('source_message_id') == aid
            equal = text.strip() == original.strip()
            # A conservative exact shared passage; it does not assert copied text.
            import re
            phrases = [x.strip() for x in re.split(r'[。.!?\n]', original) if len(x.strip()) >= 18]
            matches = [phrase for phrase in phrases if phrase in text]
            if explicit or equal or matches:
                links.append({'answer_id': aid, 'candidate_record_id': record['id'],
                    'answer': catalog[aid], 'candidate_action': catalog[record['id']],
                    'relation': '활용을 명시함' if explicit else '표현이 일치함',
                    'form': '전체 표현 일치' if equal else '다른 내용으로 공유·반영' if explicit else '일부 표현 일치',
                    'matching_quotes': matches, 'verified': None,
                    'limitation': '표현 일치나 수정만으로 복사·검증 완료를 단정하지 않습니다.'})
    return links


def candidate_action_in_window(row, evidence):
    if evidence.get('actor') != 'candidate' or not row['window'][0] <= evidence.get('elapsed', -1) <= row['window'][1]:
        return False
    data = evidence.get('data', {})
    if row['axis'] == 'AI 활용 역량':
        if evidence['kind'] == 'message' and data.get('flow') == 'chat':
            return True
        # Explicit follow-up use is eligible; raw collaboration alone is insufficient.
        return evidence['kind'] in ['message', 'part', 'task'] and bool(data.get('source_message_id'))
    if evidence['kind'] == 'message':
        return data.get('flow') == 'collaboration'
    return evidence['kind'] in ['part', 'division', 'task']


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item: str
    observation: str
    candidate_ids: list[str] = Field(min_length=1, max_length=6)
    supporting_ids: list[str] = Field(default_factory=list, max_length=6)


async def generate(snapshot, reserve=None):
    catalog = evidence_catalog(snapshot)
    rows = baseline(snapshot)
    links = utilization_links(snapshot)
    observable = []
    for row in rows:
        eligible = [eid for eid, evidence in catalog.items() if candidate_action_in_window(row, evidence)]
        if eligible and row['status'] not in ['관찰 기회 없음', '기록 불충분', '사용하지 않음']:
            observable.append({k: row[k] for k in ['axis', 'item', 'situation', 'window']} |
                              {'eligible_candidate_ids': eligible})
    aliases = {eid: ('F' if e['kind']=='final_part' else 'C' if e['actor']=='candidate' else 'S')+str(i).zfill(4) for i,(eid,e) in enumerate(catalog.items())}
    def view(e):
        quote=e['quote']
        if e['kind']=='part':
            quote='이전: '+e['data']['before']['body']+'\n변경: '+e['data']['after']['body']
        elif e['kind']=='task':
            quote=json.dumps({k:e['data']['after'][k] for k in ['title','owner','status','note','version']},ensure_ascii=False)
        return {'id':aliases[e['id']], 'actor':e['actor'], 'kind':e['kind'], 'elapsed':e['elapsed'], 'quote':quote,
                'title':e['data'].get('title', ''), 'version':e['data'].get('version')}
    for item in observable:
        item['eligible_candidate_ids']=[aliases[eid] for eid in item['eligible_candidate_ids']]
    prompt = {'items': observable,
              'candidate_evidence': [view(e) for e in catalog.values() if e['actor']=='candidate' and e['kind']!='final_part'],
              'supporting_evidence': [view(e) for e in catalog.values() if e['actor']!='candidate' and e['kind']!='final_part'],
              'final_output': [view(e) for e in catalog.values() if e['kind']=='final_part'],
              'quality_criteria': (snapshot.get('contract') or {}).get('quality_criteria', []),
              'conditions': session_conditions(snapshot),
              'utilization_links': [{k: aliases.get(v,v) if isinstance(v,str) else v for k, v in link.items() if k not in ['answer', 'candidate_action']}
                                    for link in links]}
    if len(json.dumps(prompt, ensure_ascii=False)) > 70000:
        raise ModelError('report_input_budget_exceeded')
    request = Request(binding=Binding.model_validate(snapshot.get('report_binding') or snapshot['binding']), tools=[], timeout=120, max_output_tokens=6500,
        messages=[{'role': 'system', 'content':
            ('근거 리포트의 문장만 정리합니다. 도구 실행 금지. ' +
             ('JSON 객체 {"observations": [...], "quality": [...]}를 반환하세요. quality는 요구사항 반영/근거 활용/실행 가능성 3개이며 각 원소는 item, rationale, supporting_final_ids, conflicting_final_ids, checks입니다. '
              '최종본 전체를 비교하세요. final_output의 ID만 품질 근거로 사용하고 실제 제공 조건·정책 원문과 대조하세요. '
              'checks는 사람이 확인할 불확실성의 문자열 배열입니다. rationale에 지지/상충 관계를 설명하고 평가 등급을 만들지 마세요. '
              if snapshot.get('version', 1)>=2 else 'JSON 배열을 반환하세요. ')) +
            'observations의 각 원소는 item, observation, candidate_ids, supporting_ids만 포함합니다. '
            'candidate_ids는 지원자가 실제로 한 행동이며 supporting_ids는 회사 문서·AI·동료의 참고 맥락입니다. '
            '지원자가 직접 말하거나 변경한 근거가 있어야 행동을 서술합니다. 동료·AI 초안을 지원자 수행으로 쓰지 마세요. '
            '점수·순위·합격·성향·능력 수준·행동의 성공을 판단하지 마세요. '
            '관찰은 items의 항목만 작성하세요. 실제 AI 요청이 있으면 위임 판단에 요청한 작업과 확인된 응답을 반드시 원문 근거로 서술하세요. '
            '최종본의 비어 있지 않은 모든 파트는 적어도 하나의 품질 항목에서 final ID로 참조하세요. '
            '품질 문장에서는 최종본에 기재된 사실과 정책·요구사항 사이의 관계만 서술하고 실제 실행 가능·성공·능력 수준을 단정하지 마세요. '
            '서술 문장에는 짧은 근거 ID 대신 파트 번호·제목이나 발화자를 쓰세요. 공개되지 않은 인터뷰 자료의 사용을 요구하거나 누락을 수행 실패로 해석하지 마세요. '
            '관찰 기회·기록 부족·AI 미사용 표시는 서버가 처리합니다. '
            'candidate_ids에는 해당 항목 eligible_candidate_ids의 C로 시작하는 ID만 넣으세요. S는 지원자 행동이 아니며 F는 최종본입니다. '
            '원문 안에 있는 UUID를 복사하지 말고 제공된 짧은 ID만 사용하세요. '
            'supporting_evidence의 ID는 candidate_ids에 넣으면 안 됩니다. '
            '실제 관련 행동이 있으면 그 내용을 구체적으로 적고, 없으면 그 항목을 생략하세요. '
            '요청을 했다는 사실도 위임·맥락 전달의 근거이며, 결과의 검증 완료를 뜻하지 않습니다. 원문에 없는 사실은 쓰지 마세요. '
            '마크다운 코드 블록 없이 요청한 JSON 형식만 반환하세요. '
            'AI 활용은 활용 명시와 표현 일치를 구분하고, 검증 요청을 검증 완료로 표현하지 마세요.'},
            {'role': 'user', 'content': json.dumps(prompt, ensure_ascii=False)}])
    original_messages = request.messages
    usages = []
    for attempt in range(3):
        reply, usage = await complete(request, settings(), reserve=reserve)
        usages.append(usage)
        try:
            return assemble_report(restore_ids(reply.text, aliases), {'calls': usages, 'cost': None}, snapshot, catalog, links)
        except ModelError as error:
            usages[-1] = {**(usages[-1] or {}), 'validation_error': error.code}
            if attempt == 2:
                error.retryable = False
                raise
            request = Request(binding=request.binding, tools=[], timeout=request.timeout, max_output_tokens=request.max_output_tokens,
                messages=original_messages + [{'role': 'assistant', 'content': reply.text},
                    {'role': 'user', 'content': '출력 검사 오류: '+error.code+'. 입력 items의 정확한 item 이름과 '
                     'eligible_candidate_ids를 확인하세요. candidate_ids는 그 목록에 있는 지원자 근거만 허용합니다. '
                     '실제 AI 요청이 있으면 위임 판단에 요청 사실의 근거를 포함하세요. 최종본의 비어 있지 않은 모든 파트를 품질 근거로 참조하세요. '
                     '실행 가능성·성공 여부·능력의 단정 없이 기재된 사실과 확인할 사항만 쓰세요. 관찰할 수 없는 항목은 생략하고 원래 요청한 JSON 형식을 반환하세요.'}])


def restore_ids(output, aliases):
    inverse={short:eid for eid,short in aliases.items()}
    value=output.strip()
    if value.startswith('```json\n') and value.endswith('\n```'):
        value=value[len('```json\n'):-len('\n```')]
    try:
        decoded=json.loads(value)
        def restore(obj):
            if isinstance(obj,list):return [restore(v) for v in obj]
            if isinstance(obj,dict):return {k:([inverse.get(v,v) for v in values] if k in ['candidate_ids','supporting_ids','supporting_final_ids','conflicting_final_ids'] and isinstance(values,list) else restore(values)) for k,values in obj.items()}
            return obj
        return json.dumps(restore(decoded),ensure_ascii=False)
    except (ValueError,TypeError):
        return output  # The same strict assembler rejects malformed JSON.


def assemble_report(output, usage, snapshot, catalog, links):
    rows = baseline(snapshot)
    try:
        output = output.strip()
        if output.startswith('```json\n') and output.endswith('\n```'):
            output = output[len('```json\n'):-len('\n```')]
        decoded = json.loads(output)
        quality = decoded.get('quality') if isinstance(decoded, dict) else None
        observations = decoded.get('observations') if isinstance(decoded, dict) else decoded
        selections = [Selection.model_validate(s) for s in observations]
    except (ValueError, TypeError):
        raise ModelError('invalid_report_output') from None
    seen = set()
    for selection in selections:
        if selection.item in seen:
            raise ModelError('duplicate_report_item')
        seen.add(selection.item)
        row = next((r for r in rows if r['item']==selection.item), None)
        if not row or row['status'] in ['관찰 기회 없음', '사용하지 않음', '기록 불충분']:
            raise ModelError('unsupported_report_observation')
        if any(eid not in catalog or not candidate_action_in_window(row, catalog[eid])
               for eid in selection.candidate_ids):
            raise ModelError('candidate_evidence_required')
        selected = []
        for eid in dict.fromkeys(selection.candidate_ids + selection.supporting_ids):
            if eid not in catalog:
                raise ModelError('unknown_report_evidence')
            selected.append(catalog[eid])
        if not any(candidate_action_in_window(row, e) for e in selected):
            raise ModelError('candidate_evidence_required')
        row.update(status='기록 있음', observation=selection.observation, evidence=selected)
    report = {'scenario_version': snapshot.get('scenario_version', 'looplab-v1'), 'version': snapshot['version'], 'binding': snapshot.get('report_binding') or snapshot['binding'], 'items': rows,
              'utilization_links': links, 'usage': usage, 'limits': '원문 인용 검사는 의미 해석의 정확성을 보장하지 않습니다. 최종 판단은 사람이 합니다.'}
    if snapshot.get('version', 1) >= 2:
        report.update(contract=snapshot['contract'], conditions=session_conditions(snapshot), scenario=snapshot.get('scenario'),
            final_parts=snapshot['parts'], tasks=snapshot.get('tasks', []),
            quality_comparison=quality_rows(quality, snapshot, catalog),
            process_links=process_links(snapshot), prompt_threads=prompt_threads(snapshot),
            process_evidence=process_evidence(snapshot))
        if any(candidate_action_in_window(row, e) for row in rows for e in catalog.values()) and not any(r['observation'] for r in rows):
            raise ModelError('empty_report_observations')
    if snapshot.get('report_binding'):
        report['agent_binding'] = snapshot['binding']
    validate_report(report, snapshot)
    return report


def validate_report(report, snapshot):
    if not report or report.get('version')!=snapshot['version'] or report.get('binding')!=(snapshot.get('report_binding') or snapshot['binding']):
        raise ModelError('invalid_report_binding')
    if snapshot.get('report_binding') and report.get('agent_binding') != snapshot['binding']:
        raise ModelError('invalid_agent_binding')
    if len(report.get('items', []))!=10:
        raise ModelError('invalid_report_items')
    if report.get('utilization_links', []) != utilization_links(snapshot):
        raise ModelError('invalid_utilization_evidence')
    catalog = evidence_catalog(snapshot)
    expected = {(a, i) for a, i, _ in ITEMS}
    if {(r.get('axis'),r.get('item')) for r in report['items']} != expected:
        raise ModelError('invalid_report_items')
    from dayzero.agents.harness import guard_text
    expected_rows = {r['item']: r for r in baseline(snapshot)}
    for row in report['items']:
        base = expected_rows[row['item']]
        if row.get('window') != base['window'] or row.get('situation') != base['situation']:
            raise ModelError('invalid_report_window')
        if row.get('status') not in {base['status'], '기록 있음'}:
            raise ModelError('invalid_report_status')
        if row['observation']:
            guard_text(row['observation'])
            if not row['evidence']:
                raise ModelError('report_evidence_required')
            if base['status'] in ['관찰 기회 없음', '사용하지 않음', '기록 불충분'] or not any(
                    candidate_action_in_window(row, e) for e in row['evidence']):
                raise ModelError('candidate_evidence_required')
        for evidence in row['evidence']:
            original = catalog.get(evidence['id'])
            if evidence != original:
                raise ModelError('report_evidence_mismatch')

    if snapshot.get('version', 1) >= 2:
        if report.get('contract') != snapshot.get('contract') or report.get('conditions') != session_conditions(snapshot):
            raise ModelError('invalid_report_conditions')
        if report.get('final_parts') != snapshot['parts'] or report.get('tasks') != snapshot.get('tasks', []):
            raise ModelError('invalid_report_final_output')
        if report.get('process_links') != process_links(snapshot) or report.get('prompt_threads') != prompt_threads(snapshot) or report.get('process_evidence') != process_evidence(snapshot):
            raise ModelError('invalid_report_process')
        validate_quality(report.get('quality_comparison'), snapshot, catalog)
        if any(r['kind']=='message' and r['actor']=='candidate' and r['data'].get('flow')=='chat' for r in snapshot['records']) and not any(g['flow']=='chat' for g in snapshot.get('record_gaps', [])) and not any(r['axis']=='AI 활용 역량' and r['observation'] for r in report['items']):
            raise ModelError('ai_observation_required')
        if any(candidate_action_in_window(row, e) for row in report['items'] for e in catalog.values()) and not any(r['observation'] for r in report['items']):
            raise ModelError('empty_report_observations')


def session_conditions(snapshot):
    delivered = {e['key'] for e in snapshot['events'] if e.get('received_at')}
    scenario = snapshot.get('scenario') or {}
    target = (scenario.get('changed_target') or '3개월 넘게 모임에 안 나온 사용자') if 'change' in delivered else scenario.get('initial_target', '첫 참여자')
    return {'scenario_version': snapshot.get('scenario_version', 'looplab-v1'),
        'target': target,
        'duration_seconds': snapshot['ended']-snapshot['started'],
        'deadline_seconds': 7200, 'early_submission': snapshot['ended']-snapshot['started'] < 7200,
        'delivered_events': sorted(delivered),
        'unconfirmed_events': [e['key'] for e in snapshot['events'] if e.get('stored_at') and not e.get('received_at')],
        'record_gaps': snapshot.get('record_gaps', []),
        'limitation': '실제 전달된 조건만 적용합니다. 생성 응답·자료 전달 차이와 장애가 비교를 제한합니다.'}


def process_links(snapshot):
    result = []
    for record in snapshot['records']:
        data = record['data']
        references = {k: data[k] for k in ['reply_to', 'source_message_id', 'task_id', 'part_record_id', 'mutation_ids', 'source_id', 'job_id'] if data.get(k)}
        if record['kind'] == 'task':
            references.update({k: data['after'][k] for k in ['part_id', 'source_id'] if data['after'].get(k)})
        if references:
            result.append({'record_id': record['id'], 'actor': record['actor'], 'kind': record['kind'],
                'elapsed': record['elapsed'], 'references': references})
    return result


def prompt_threads(snapshot):
    requests = [r for r in snapshot['records'] if r['kind']=='message' and r['actor']=='candidate' and r['data'].get('flow')=='chat']
    return [{'request_id': r['id'], 'previous_request_id': requests[i-1]['id'] if i else None,
        'response_ids': [a['id'] for a in snapshot['records'] if a['kind']=='message' and a['actor']=='assistant' and a['data'].get('reply_to')==r['id']],
        'utilization_record_ids': [link['candidate_record_id'] for link in utilization_links(snapshot)
            if any(a['id']==link['answer_id'] and a['data'].get('reply_to')==r['id'] for a in snapshot['records'])]}
        for i, r in enumerate(requests)]


def process_evidence(snapshot):
    # Full originals let reviewers inspect prompts, replies and edits omitted by
    # the model's selection. No model-authored quotes enter this timeline.
    return [e for e in evidence_catalog(snapshot).values() if e['kind'] not in ['company_document', 'final_part']]


class QualitySelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item: str
    rationale: str = Field(min_length=1, max_length=4000)
    supporting_final_ids: list[str] = Field(max_length=5)
    conflicting_final_ids: list[str] = Field(max_length=5)
    checks: list[str] = Field(max_length=10)


def quality_rows(raw, snapshot, catalog):
    try:
        choices = [QualitySelection.model_validate(q) for q in raw]
    except (ValueError, TypeError):
        raise ModelError('quality_evidence_required') from None
    result = []
    basis = quality_basis(snapshot, catalog)
    for choice in choices:
        ids = choice.supporting_final_ids+choice.conflicting_final_ids
        if any(eid not in catalog or catalog[eid]['kind']!='final_part' or not catalog[eid]['quote'].strip() for eid in ids):
            raise ModelError('invalid_quality_evidence')
        if any(p['body'].strip() for p in snapshot['parts']) and not ids:
            raise ModelError('quality_evidence_required')
        result.append({'item': choice.item, 'rationale': choice.rationale,
            'supporting': [catalog[eid] for eid in choice.supporting_final_ids],
            'conflicting': [catalog[eid] for eid in choice.conflicting_final_ids],
            'comparison_basis': basis, 'checks': choice.checks,
            'missing_parts': [p['number'] for p in snapshot['parts'] if not p['body'].strip()],
            'limitation': '최종본 전체의 사실과 작성 주체를 보여줍니다. 지원자의 직접 기여와 의미 판단은 과정·원문을 함께 확인하세요.'})
    validate_quality(result, snapshot, catalog)
    return result


def validate_quality(rows, snapshot, catalog):
    if not isinstance(rows, list) or len(rows)!=3 or {r.get('item') for r in rows}!={'요구사항 반영', '근거 활용', '실행 가능성'}:
        raise ModelError('invalid_quality_items')
    from dayzero.agents.harness import guard_text
    covered=set()
    for row in rows:
        import re
        if re.search(r'실제로?\s*실행\s*가능|실행\s*가능하게|성공적으로|우수한|뛰어난|높은\s*역량', row.get('rationale', '')):
            raise ModelError('unsupported_quality_judgment')
        guard_text(row.get('rationale', ''))
        for check in row.get('checks', []):
            guard_text(check)
        if row.get('missing_parts') != [p['number'] for p in snapshot['parts'] if not p['body'].strip()]:
            raise ModelError('invalid_quality_gaps')
        if row.get('comparison_basis') != quality_basis(snapshot, catalog):
            raise ModelError('invalid_quality_basis')
        final = row.get('supporting', [])+row.get('conflicting', [])
        if any(p['body'].strip() for p in snapshot['parts']) and not final:
            raise ModelError('quality_evidence_required')
        for evidence in final+row.get('comparison_basis', []):
            if catalog.get(evidence.get('id')) != evidence:
                raise ModelError('invalid_quality_evidence')
        if any(e['kind']!='final_part' for e in final):
            raise ModelError('invalid_quality_evidence')
        covered.update(e['id'] for e in final)
    expected={eid for eid,e in catalog.items() if e['kind']=='final_part' and e['quote'].strip()}
    if covered != expected:
        raise ModelError('final_output_not_covered')


def quality_basis(snapshot, catalog):
    return [e for e in catalog.values() if e['kind']=='company_document' or
        (e['kind']=='message' and e['data'].get('requirement') and
         (e['data'].get('event_key')!='change' or 'change' in session_conditions(snapshot)['delivered_events']))]
