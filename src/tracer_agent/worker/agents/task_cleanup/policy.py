"""task-cleanup의 결정 검증과 조건부 분기 정책을 소유한다."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal, TypedDict

from tracer_agent.shared.agents.task_cleanup.models import (
    CleanupCandidate,
    CleanupDraftSuggestion,
    TaskCleanupState,
)

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.routing import build_validation_router

ValidationRoute = Callable[[TaskCleanupState], Literal["repair", "finalize", "empty"]]

# SDK 축의 buildCleanupCandidates와 값을 맞춘 상수다.
CLEANUP_RECENT_ACTIVITY = timedelta(minutes=30)
CLEANUP_STALE = timedelta(days=14)
_RUNNING_STATUS = "running"
_WAITING_STATUS = "waiting"
_PLACEHOLDER_TITLE_PATTERN = re.compile(
    r"^(test|fix\s*bug|todo|wip|session started|정리해줘|테스트|임시)$", re.IGNORECASE
)


class CleanupTaskSnapshot(TypedDict):
    """후보 판정이 보는 태스크의 순수 표현이다."""

    id: str
    title: str
    status: str
    lastEventAt: datetime | None
    updatedAt: datetime


def qualify_candidates(
    tasks: list[CleanupTaskSnapshot], active_child_counts: dict[str, int], now: datetime
) -> list[CleanupCandidate]:
    """서버가 결정론적으로 정리 후보를 계산하며 SDK 축의 buildCleanupCandidates와 같은 규칙을 쓴다."""
    title_counts: dict[str, int] = {}
    for task in tasks:
        key = _normalize_title(task["title"])
        title_counts[key] = title_counts.get(key, 0) + 1

    candidates: list[CleanupCandidate] = []
    for task in tasks:
        candidate = _qualify_one(task, active_child_counts.get(task["id"], 0), title_counts, now)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _qualify_one(
    task: CleanupTaskSnapshot, active_child_count: int, title_counts: dict[str, int], now: datetime
) -> CleanupCandidate | None:
    if active_child_count > 0:
        return None
    last_activity = task["lastEventAt"] or task["updatedAt"]
    if now - last_activity < CLEANUP_RECENT_ACTIVITY:
        return None

    has_events = task["lastEventAt"] is not None
    reasons = _candidate_reasons(task, has_events, title_counts, now, last_activity)
    if not reasons:
        return None

    return CleanupCandidate(
        id=task["id"],
        visibleTitle=task["title"],
        status=task["status"],
        lastEventAt=_iso(task["lastEventAt"]) if task["lastEventAt"] is not None else None,
        hasEvents=has_events,
        activeChildCount=active_child_count,
        candidateReasons=reasons,
    )


def _candidate_reasons(
    task: CleanupTaskSnapshot,
    has_events: bool,
    title_counts: dict[str, int],
    now: datetime,
    last_activity: datetime,
) -> list[str]:
    reasons: list[str] = []
    if not has_events:
        reasons.append("no-events")
    if title_counts.get(_normalize_title(task["title"]), 0) > 1:
        reasons.append("duplicate-title")
    if _PLACEHOLDER_TITLE_PATTERN.fullmatch(task["title"].strip()):
        reasons.append("placeholder-title")
    is_active_status = task["status"] in (_RUNNING_STATUS, _WAITING_STATUS)
    if is_active_status and now - last_activity >= CLEANUP_STALE:
        reasons.append("stale")
    return reasons


def _normalize_title(title: str) -> str:
    return title.strip().lower()


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def validate_suggestions(
    suggestions: list[CleanupDraftSuggestion],
    state: TaskCleanupState,
) -> tuple[list[CleanupDraftSuggestion], list[str]]:
    """정리 제안이 도구가 돌려준 후보와 이벤트만 인용하는지 검증한다."""
    exposed = state["exposed_candidates"]
    valid: list[CleanupDraftSuggestion] = []
    errors: list[str] = []
    seen: set[str] = set()
    for suggestion in suggestions:
        item_errors: list[str] = []
        candidate = exposed.get(suggestion.taskId)
        if candidate is None:
            item_errors.append(f"unsupported candidate task ID {suggestion.taskId}")
        if suggestion.taskId in seen:
            item_errors.append(f"duplicate suggestion for task {suggestion.taskId}")
        seen.add(suggestion.taskId)
        inspected = suggestion.taskId in state["event_ids_by_task"]
        supported_events = state["event_ids_by_task"].get(suggestion.taskId, set())
        cited_events = set(suggestion.evidenceEventIds)
        unknown_events = sorted(cited_events - supported_events)
        if unknown_events:
            item_errors.append(
                f"unsupported event IDs for task {suggestion.taskId}: {', '.join(unknown_events)}"
            )
        if candidate is not None and candidate.hasEvents and not inspected:
            item_errors.append(f"eventful task {suggestion.taskId} was never inspected")
        elif supported_events and not cited_events:
            item_errors.append(f"eventful task {suggestion.taskId} has no inspected event evidence")
        if len(valid) >= state["max_suggestions"]:
            item_errors.append(f"suggestion limit {state['max_suggestions']} exceeded")
        if item_errors:
            errors.extend(item_errors)
        else:
            valid.append(suggestion)
    return valid, errors


def build_routes(trace: ExecutionTrace, validation_node: str) -> ValidationRoute:
    """검증 결과에 따른 분기 함수를 만든다."""
    return build_validation_router(
        trace,
        validation_node,
        pass_reason="suggestions passed deterministic provenance validation",
        repair_reason="suggestions failed validation and one repair attempt remains",
        exhausted_reason="invalid suggestions were dropped after the repair attempt",
        has_result=lambda state: bool(state["suggestions"]),
    )
