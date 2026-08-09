"""task-cleanup 정책 함수의 제안 검증 규칙을 고정한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tests.support.contract import agent_cases
from tracer_agent.shared.agents.task_cleanup.models import (
    CleanupCandidate,
    CleanupDraftSuggestion,
    TaskCleanupState,
)
from tracer_agent.worker.agents.task_cleanup.candidates import (
    CleanupTaskSnapshot,
    qualify_candidates,
    without_active_children,
)
from tracer_agent.worker.agents.task_cleanup.policy import filter_valid_suggestions


def _candidate(task_id: str, *, has_events: bool) -> CleanupCandidate:
    return CleanupCandidate(
        id=task_id,
        visibleTitle=f"제목 {task_id}",
        status="running",
        lastEventAt=None,
        hasEvents=has_events,
        activeChildCount=0,
        candidateReasons=["stale"],
    )


def _state(
    *,
    exposed: dict[str, CleanupCandidate],
    event_ids: dict[str, set[str]],
    max_suggestions: int = 5,
) -> TaskCleanupState:
    return {
        "scanned_at": "2026-07-14T00:00:00Z",
        "language": "ko",
        "max_suggestions": max_suggestions,
        "messages": [],
        "plan": None,
        "redispatch": None,
        "redispatch_ceiling": 0.0,
        "redispatch_count": 0,
        "reports": [],
        "exposed_candidates": exposed,
        "event_ids_by_task": event_ids,
        "model_cost_usd": 0.0,
        "suggestions": [],
    }


def test_노출되지_않은_후보와_읽지_않은_이벤트_후보를_버리고_인용이_맞는_후보만_남긴다() -> None:
    state = _state(
        exposed={
            "task-1": _candidate("task-1", has_events=True),
            "task-2": _candidate("task-2", has_events=True),
        },
        event_ids={"task-1": {"event-1"}},
    )
    suggestions = [
        CleanupDraftSuggestion(
            kind="archive", taskId="task-1", rationale="의미 있는 활동이 없다", evidenceEventIds=["event-1"]
        ),
        CleanupDraftSuggestion(
            kind="archive", taskId="task-2", rationale="근거 없이 제안", evidenceEventIds=[]
        ),
        CleanupDraftSuggestion(kind="archive", taskId="ghost", rationale="없는 태스크", evidenceEventIds=[]),
    ]

    valid, errors = filter_valid_suggestions(suggestions, state)

    # 검토자가 읽고 인용까지 맞춘 후보만 유지된다.
    assert [item.taskId for item in valid] == ["task-1"]
    assert any("unsupported candidate task ID ghost" in error for error in errors)
    assert any("task-2 was never inspected" in error for error in errors)


def _task(
    task_id: str,
    title: str,
    *,
    status: str = "completed",
    last_event_at: datetime | None,
    updated_at: datetime,
) -> CleanupTaskSnapshot:
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "lastEventAt": last_event_at,
        "updatedAt": updated_at,
    }


_NOW = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
_OLD = _NOW - timedelta(days=30)


def test_최근에_활동한_태스크는_후보에서_빠진다() -> None:
    recent = _task("t1", "제목", last_event_at=_NOW - timedelta(minutes=1), updated_at=_NOW)

    candidates = qualify_candidates([recent], _NOW)

    assert candidates == []


def test_활성_자식이_있는_태스크는_사유가_있어도_후보에서_빠진다() -> None:
    task = _task("t1", "test", last_event_at=None, updated_at=_OLD)

    candidates = without_active_children(qualify_candidates([task], _NOW), {"t1": 1})

    assert candidates == []


def test_이벤트가_없으면_no_events_사유가_붙는다() -> None:
    task = _task("t1", "제목", last_event_at=None, updated_at=_OLD)

    candidates = qualify_candidates([task], _NOW)

    assert len(candidates) == 1
    assert candidates[0].candidateReasons == ["no-events"]
    assert candidates[0].hasEvents is False


def test_같은_제목이_둘_이상이면_duplicate_title_사유가_붙는다() -> None:
    tasks = [
        _task("t1", "같은 제목", last_event_at=_OLD, updated_at=_OLD),
        _task("t2", "같은 제목", last_event_at=_OLD, updated_at=_OLD),
    ]

    candidates = qualify_candidates(tasks, _NOW)

    assert {c.id for c in candidates} == {"t1", "t2"}
    for candidate in candidates:
        assert "duplicate-title" in candidate.candidateReasons


def test_자리표시자_제목은_placeholder_title_사유가_붙는다() -> None:
    task = _task("t1", "  TODO  ", last_event_at=_OLD, updated_at=_OLD)

    candidates = qualify_candidates([task], _NOW)

    assert candidates[0].candidateReasons == ["placeholder-title"]


def test_활성_상태로_오래_멈춘_태스크는_stale_사유가_붙는다() -> None:
    task = _task(
        "t1",
        "제목",
        status="running",
        last_event_at=_NOW - timedelta(days=15),
        updated_at=_NOW - timedelta(days=15),
    )

    candidates = qualify_candidates([task], _NOW)

    assert candidates[0].candidateReasons == ["stale"]


def test_사유가_하나도_없으면_후보에서_빠진다() -> None:
    task = _task("t1", "고유한 제목", status="completed", last_event_at=_OLD, updated_at=_OLD)

    candidates = qualify_candidates([task], _NOW)

    assert candidates == []


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _expected_task(raw: dict[str, Any]) -> CleanupTaskSnapshot:
    return {
        "id": raw["id"],
        "title": raw["title"],
        "status": raw["status"],
        "lastEventAt": _parse_iso(raw["lastEventAt"]) if raw["lastEventAt"] is not None else None,
        "updatedAt": _parse_iso(raw["updatedAt"]),
    }


def test_TypeScript_구현체와_공유하는_후보_판정이_계약의_케이스와_같은_판정을_낸다() -> None:
    golden = agent_cases("task-cleanup")["candidateCases"]
    now = _parse_iso(golden["now"])

    for case in golden["cases"]:
        tasks = [_expected_task(raw) for raw in case["tasks"]]
        active_child_counts: dict[str, int] = {}
        for parent_id in case["activeChildParentIds"]:
            active_child_counts[parent_id] = active_child_counts.get(parent_id, 0) + 1

        candidates = without_active_children(qualify_candidates(tasks, now), active_child_counts)

        assert sorted(c.id for c in candidates) == sorted(case["expectedIds"]), case["name"]
        for task_id, reasons in case.get("expectedReasons", {}).items():
            candidate = next((c for c in candidates if c.id == task_id), None)
            assert candidate is not None
            assert candidate.candidateReasons == reasons, case["name"]
