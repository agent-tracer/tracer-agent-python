"""task-cleanup의 정리 후보를 추적 창구가 준 태스크 목록에서 결정론적으로 계산한다."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TypedDict

from tracer_agent.shared.agents.task_cleanup.models import (
    CandidateReason,
    CleanupCandidate,
    CleanupTaskStatus,
)

# 정리 후보를 최근 활동으로 거를 때 쓰는 상한이며 두 축이 같은 값을 쓴다.
CLEANUP_RECENT_ACTIVITY = timedelta(minutes=30)
CLEANUP_STALE = timedelta(days=14)
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


def qualify_candidates(tasks: list[CleanupTaskSnapshot], now: datetime) -> list[CleanupCandidate]:
    """서버가 결정론적으로 정리 후보를 계산하며 SDK 축의 buildCleanupCandidates와 같은 규칙을 쓴다."""
    title_counts: dict[str, int] = {}
    for task in tasks:
        key = _normalize_title(task["title"])
        title_counts[key] = title_counts.get(key, 0) + 1

    candidates: list[CleanupCandidate] = []
    for task in tasks:
        candidate = _qualify_one(task, title_counts, now)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def without_active_children(
    candidates: list[CleanupCandidate], active_child_counts: dict[str, int]
) -> list[CleanupCandidate]:
    """자식이 아직 진행 중인 태스크는 정리 대상이 아니므로 후보에서 뺀다."""
    return [candidate for candidate in candidates if active_child_counts.get(candidate.id, 0) == 0]


def _qualify_one(
    task: CleanupTaskSnapshot, title_counts: dict[str, int], now: datetime
) -> CleanupCandidate | None:
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
        activeChildCount=0,
        candidateReasons=reasons,
    )


def _candidate_reasons(
    task: CleanupTaskSnapshot,
    has_events: bool,
    title_counts: dict[str, int],
    now: datetime,
    last_activity: datetime,
) -> list[CandidateReason]:
    reasons: list[CandidateReason] = []
    if not has_events:
        reasons.append(CandidateReason.NO_EVENTS)
    if title_counts.get(_normalize_title(task["title"]), 0) > 1:
        reasons.append(CandidateReason.DUPLICATE_TITLE)
    if _PLACEHOLDER_TITLE_PATTERN.fullmatch(task["title"].strip()):
        reasons.append(CandidateReason.PLACEHOLDER_TITLE)
    is_active_status = task["status"] in tuple(CleanupTaskStatus)
    if is_active_status and now - last_activity >= CLEANUP_STALE:
        reasons.append(CandidateReason.STALE)
    return reasons


def _normalize_title(title: str) -> str:
    return title.strip().lower()


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
