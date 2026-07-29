"""recipe-scan 도구 결과에서 검증 가능한 근거 식별자를 카탈로그에 누적한다."""

from __future__ import annotations

import json

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog


def loaded(content: str) -> object | None:
    """도구가 돌려준 본문을 JSON으로 읽되 본문이 아니면 근거로 삼지 않는다."""
    try:
        parsed: object = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed


def add_events(catalog: ProvenanceCatalog, raw_events: object, default_task_id: str | None) -> None:
    """이벤트 응답 본문에서 태스크별 이벤트와 turn 식별자를 기록한다."""
    if not isinstance(raw_events, list):
        return
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        task_id = raw.get("taskId") or default_task_id
        event_id = raw.get("id")
        turn_id = raw.get("turnId")
        if not isinstance(task_id, str) or not task_id or not isinstance(event_id, str) or not event_id:
            continue
        catalog.eventIdsByTask.setdefault(task_id, set()).add(event_id)
        if isinstance(turn_id, str) and turn_id:
            catalog.turnIdsByTask.setdefault(task_id, set()).add(turn_id)


def add_rule_ids(catalog: ProvenanceCatalog, parsed: object) -> None:
    """규칙 목록 응답에서 규칙 식별자를 기록한다."""
    if not isinstance(parsed, list):
        return
    for value in parsed:
        if isinstance(value, dict):
            _add_string(catalog.ruleIds, value.get("id"))


def add_recipe_revs(catalog: ProvenanceCatalog, parsed: object) -> None:
    """개정 번호가 있는 레시피만 수정 근거로 인정해 식별자와 그 개정 번호를 기록한다."""
    if not isinstance(parsed, list):
        return
    for value in parsed:
        if not isinstance(value, dict):
            continue
        revision = value.get("rev")
        recipe_id = value.get("id")
        if not isinstance(revision, int) or isinstance(revision, bool):
            continue
        if isinstance(recipe_id, str) and recipe_id:
            catalog.recipeRevs[recipe_id] = revision


def _add_string(target: set[str], value: object) -> None:
    if isinstance(value, str) and value:
        target.add(value)
