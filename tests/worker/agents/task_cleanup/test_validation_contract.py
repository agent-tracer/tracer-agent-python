"""task-cleanup 제안 검증 판정을 계약의 케이스로 검증한다."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.contract import agent_spec
from tracer_agent.shared.agents.task_cleanup.models import (
    CleanupCandidate,
    CleanupDraftSuggestion,
    TaskCleanupState,
)
from tracer_agent.worker.agents.task_cleanup.policy import validate_suggestions

_CONTRACT = agent_spec("task-cleanup")["cases"]


def _state(case: dict[str, Any]) -> TaskCleanupState:
    exposed = {str(raw["id"]): CleanupCandidate.model_validate(raw) for raw in _CONTRACT["candidates"]}
    return {
        "scanned_at": "2026-07-14T00:00:00Z",
        "language": "ko",
        "max_suggestions": case.get("maxSuggestions", _CONTRACT["maxSuggestions"]),
        "messages": [],
        "plan": None,
        "redispatch": None,
        "redispatch_ceiling": 0.0,
        "redispatch_count": 0,
        "reports": [],
        "exposed_candidates": exposed,
        "event_ids_by_task": {task_id: set(event_ids) for task_id, event_ids in case["inspected"].items()},
        "model_cost_usd": 0.0,
        "suggestions": [],
    }


def _suggestions(case: dict[str, Any]) -> list[CleanupDraftSuggestion]:
    return [
        CleanupDraftSuggestion.model_validate({**_CONTRACT["suggestionDefaults"], **override})
        for override in case["suggestions"]
    ]


@pytest.mark.parametrize("case", _CONTRACT["cases"], ids=lambda case: str(case["name"]))
def test_계약의_케이스마다_같은_판정과_같은_사유를_낸다(case: dict[str, Any]) -> None:
    valid, errors = validate_suggestions(_suggestions(case), _state(case))

    assert [item.taskId for item in valid] == case["expect"]["validTaskIds"]
    assert errors == case["expect"]["errors"]
    assert (not errors) == case["expect"]["valid"]
