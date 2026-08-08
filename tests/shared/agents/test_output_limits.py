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

# 두 스키마가 같은 모양을 서로 다른 정의 이름으로 담으므로 뿌리부터의 칸 경로로만 맞춘다.
type Place = tuple[str, str]

# 칸을 새로 여는 자리가 아니므로 이 아래는 바깥 경로를 그대로 쓴다.
TRANSPARENT = frozenset({"anyOf", "oneOf", "allOf"})

# 같은 정의를 다시 펼치는 스키마가 있으므로 되돌아온 자리에서 멈춘다.
_MAX_DEPTH = 12


class _Walker:
    """참조를 펼쳐 뿌리부터의 칸 경로마다 걸린 수치 상한을 모은다."""

    def __init__(self, document: dict[str, Any]) -> None:
        self._defs: dict[str, Any] = document.get("$defs", {})
        self.found: dict[Place, set[int]] = {}

    def walk(self, schema: Any, path: str = "", depth: int = 0) -> None:
        if depth > _MAX_DEPTH or not isinstance(schema, dict):
            return
        reference = schema.get("$ref")
        if isinstance(reference, str):
            self.walk(self._defs.get(reference.rsplit("/", 1)[-1]), path, depth + 1)
            return
        for key in LIMIT_KEYS:
            if isinstance(schema.get(key), int):
                self.found.setdefault((path, key), set()).add(schema[key])
        for name, value in (schema.get("properties") or {}).items():
            self.walk(value, f"{path}.{name}", depth + 1)
        if "items" in schema:
            self.walk(schema["items"], f"{path}[]", depth + 1)
        for key in TRANSPARENT:
            for variant in schema.get(key) or ():
                self.walk(variant, path, depth + 1)


def _declared(document: dict[str, Any]) -> dict[Place, set[int]]:
    # not 아래는 무엇을 거절하는지를 적은 자리라 칸에 걸린 상한이 아니므로 지나지 않는다.
    walker = _Walker(document)
    walker.walk(document)
    return walker.found


@pytest.mark.parametrize(("agent_id", "draft"), DRAFTS, ids=[agent for agent, _ in DRAFTS])
def test_산출_모델의_상한이_계약이_적은_값과_같다(agent_id: str, draft: type[BaseModel]) -> None:
    declared = _declared(agent_output(agent_id)["schema"])
    built = _declared(draft.model_json_schema())

    assert declared, agent_id
    # 한 자리에서 멈추면 남은 자리를 못 보므로 어긋난 곳을 모두 모아 낸다.
    missing = {place: values for place, values in declared.items() if built.get(place) != values}

    assert not missing, f"{agent_id}: {sorted(missing)}"
