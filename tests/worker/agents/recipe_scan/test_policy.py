"""recipe-scan 정책 함수의 인용 검증 규칙을 고정한다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeCandidate
from tracer_agent.worker.agents.recipe_scan.policy import validate_recipe_candidate


def _candidate(**overrides: Any) -> RecipeCandidate:
    values: dict[str, Any] = {
        "title": "Add migration",
        "intent": "마이그레이션을 안전하게 추가한다",
        "description": "스키마 변경이 필요할 때 쓴다.",
        "summary_md": "- 변경을 정의한다\n- 검증한다",
        "request": "사용자가 마이그레이션 추가를 요청했다.",
        "contributing_slices": [{"taskId": "task-1", "eventIds": ["event-1"]}],
        "rationale": "반복 가능한 절차다.",
    }
    values.update(overrides)
    return RecipeCandidate.model_validate(values)


def test_anchor_slice는_실제_anchor_event를_인용해야_한다() -> None:
    candidate = RecipeCandidate(
        title="Add migration",
        intent="마이그레이션을 안전하게 추가한다",
        description="스키마 변경이 필요할 때 쓴다.",
        summary_md="- 변경을 정의한다\n- 검증한다",
        request="사용자가 마이그레이션 추가를 요청했다.",
        contributing_slices=[{"taskId": "task-1", "eventIds": []}],
        rationale="반복 가능한 절차다.",
    )
    provenance = ProvenanceCatalog(eventIdsByTask={"task-1": {"event-1"}, "task-2": {"event-2"}})

    errors = validate_recipe_candidate(candidate, "task-1", provenance)

    assert "The anchor contributing slice must cite at least one anchor event ID." in errors


def test_이벤트를_읽지_않은_태스크는_기여_슬라이스로_인정하지_않는다() -> None:
    candidate = RecipeCandidate(
        title="Add migration",
        intent="마이그레이션을 안전하게 추가한다",
        description="스키마 변경이 필요할 때 쓴다.",
        summary_md="- 변경을 정의한다\n- 검증한다",
        request="사용자가 마이그레이션 추가를 요청했다.",
        contributing_slices=[
            {"taskId": "task-1", "eventIds": ["event-1"]},
            {"taskId": "task-2", "eventIds": []},
        ],
        rationale="반복 가능한 절차다.",
    )
    provenance = ProvenanceCatalog(eventIdsByTask={"task-1": {"event-1"}})

    errors = validate_recipe_candidate(candidate, "task-1", provenance)

    assert "Unsupported contributing task ID: task-2." in errors


@pytest.mark.parametrize(
    "slices",
    [
        [{"taskId": "task-1", "eventIds": ["event-1"]}, {"taskId": "task-1", "eventIds": []}],
        [{"taskId": "task-1", "eventIds": []}, {"taskId": "task-1", "eventIds": ["event-1"]}],
    ],
    ids=["앞의 slice가 근거를 든다", "뒤의 slice가 근거를 든다"],
)
def test_같은_태스크의_slice들은_근거의_합집합으로_판정한다(slices: list[dict[str, Any]]) -> None:
    # 하나만 남기고 버리면 어느 쪽을 남기느냐로 두 축의 판정이 갈린다.
    candidate = _candidate(contributing_slices=slices)
    provenance = ProvenanceCatalog(eventIdsByTask={"task-1": {"event-1"}})

    errors = validate_recipe_candidate(candidate, "task-1", provenance)

    assert errors == []


def test_같은_태스크의_slice들이_모두_비면_anchor_근거가_없다고_본다() -> None:
    candidate = _candidate(
        contributing_slices=[
            {"taskId": "task-1", "eventIds": []},
            {"taskId": "task-1", "eventIds": []},
        ]
    )
    provenance = ProvenanceCatalog(eventIdsByTask={"task-1": {"event-1"}})

    errors = validate_recipe_candidate(candidate, "task-1", provenance)

    assert "The anchor contributing slice must cite at least one anchor event ID." in errors


def test_같은_태스크의_어느_slice가_들어도_관측되지_않은_이벤트는_거부한다() -> None:
    candidate = _candidate(
        contributing_slices=[
            {"taskId": "task-1", "eventIds": ["event-1"]},
            {"taskId": "task-1", "eventIds": ["event-9"]},
        ]
    )
    provenance = ProvenanceCatalog(eventIdsByTask={"task-1": {"event-1"}})

    errors = validate_recipe_candidate(candidate, "task-1", provenance)

    assert "Unsupported event IDs for task task-1: event-9." in errors


def test_공백만_남는_개정_대상은_후보로_서지_못한다() -> None:
    # 빈 문자열을 통과시키면 falsy 단축이 개정 대상 검사를 통째로 건너뛴다.
    with pytest.raises(ValidationError) as rejected:
        _candidate(revises_recipe_id="   ")

    assert {error["loc"][0] for error in rejected.value.errors()} == {"revises_recipe_id"}


def test_적은_개정_대상은_관측한_레시피여야_한다() -> None:
    candidate = _candidate(revises_recipe_id="recipe-9")
    provenance = ProvenanceCatalog(eventIdsByTask={"task-1": {"event-1"}}, recipeRevs={"recipe-1": 3})

    errors = validate_recipe_candidate(candidate, "task-1", provenance)

    assert "Unsupported revises_recipe_id: recipe-9." in errors
