"""모델에게 여는 산출 스키마의 수치 상한이 계약이 적은 값과 같은지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from tests.support.contract import agent_output
from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft
from tracer_agent.shared.agents.task_cleanup.models import CleanupDraft
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft

# 공급자가 강제하지 않으므로 두 구현체가 스스로 같은 값을 세워야 하는 칸이다.
LIMIT_KEYS = ("maxItems", "minItems", "maxLength", "minLength", "maximum", "minimum")

DRAFTS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("title-suggestion", TitleSuggestionDraft),
    ("task-cleanup", CleanupDraft),
    ("recipe-scan", RecipeDraft),
)


type Place = tuple[str, str, str]

ARRAY = "array"
ITEM = "item"

# 칸을 새로 여는 자리가 아니므로 이 아래는 바깥 칸의 이름과 자리를 그대로 쓴다.
TRANSPARENT = frozenset({"properties", "$defs", "anyOf", "oneOf", "allOf"})


def _limits(schema: Any, found: dict[Place, set[int]], name: str, scope: str) -> None:
    """스키마가 칸마다 건 수치 상한을 모으며 배열 자신과 그 항목을 갈라 적는다."""
    if isinstance(schema, list):
        for entry in schema:
            _limits(entry, found, name, scope)
        return
    if not isinstance(schema, dict):
        return
    for key in LIMIT_KEYS:
        if isinstance(schema.get(key), int):
            found.setdefault((name, key, scope), set()).add(schema[key])
    for key, value in schema.items():
        # not 아래는 무엇을 거절하는지를 적은 자리라 칸에 걸린 상한이 아니다.
        if key == "not":
            continue
        if key == "items":
            _limits(value, found, name, ITEM)
        elif key in TRANSPARENT:
            _limits(value, found, name, scope)
        else:
            _limits(value, found, key, ARRAY)


def _declared(schema: Any) -> dict[Place, set[int]]:
    found: dict[Place, set[int]] = {}
    _limits(schema, found, "", ARRAY)
    return found


@pytest.mark.parametrize(("agent_id", "draft"), DRAFTS, ids=[agent for agent, _ in DRAFTS])
def test_산출_모델의_상한이_계약이_적은_값과_같다(agent_id: str, draft: type[BaseModel]) -> None:
    declared = _declared(agent_output(agent_id)["schema"])
    built = _declared(draft.model_json_schema())

    assert declared, agent_id
    # 한 자리에서 멈추면 남은 자리를 못 보므로 어긋난 곳을 모두 모아 낸다.
    missing = {place: values for place, values in declared.items() if built.get(place) != values}

    assert not missing, f"{agent_id}: {sorted(missing)}"
