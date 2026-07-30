"""task-cleanup의 사용자 범위 이벤트 조회와, 접수가 직접 받은 요청의 후보 배치 조립을 제공한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch

from ..runtime.scoped_event_reader import ScopedEventReader
from ..runtime.tracer_client import TracerApiClient
from .policy import CleanupTaskSnapshot, qualify_candidates, without_active_children

# 조회 로직이 title-suggestion과 완전히 같아 새 서브클래스 대신 이름만 이 슬라이스로 가져온다.
CleanupLedgerReader = ScopedEventReader

# SDK 축의 TASK_SCAN_LIMIT과 값을 맞춘 상한이다.
TASK_SCAN_LIMIT = 500
SERVER_SDK_TASK_ORIGIN = "server-sdk"
TASKS_PATH = "/api/v1/tasks"
# 목록 창구가 한 장에 내주는 상한이며 배치 하나가 여러 장에 걸친다.
TASK_PAGE_LIMIT = 100
_ACTIVE_STATUSES = ("running", "waiting")


async def load_cleanup_batch(tracer: TracerApiClient, now: datetime) -> CleanupBatch:
    """SDK 축의 loadScanBatch와 같은 규칙으로 후보 배치를 추적 창구에서 조립한다."""
    tasks, batch_truncated = await _scan_tasks(tracer)
    shortlisted = qualify_candidates(tasks, now)
    counts = await _active_child_counts(tracer, [candidate.id for candidate in shortlisted])
    return CleanupBatch(
        candidates=without_active_children(shortlisted, counts), batchTruncated=batch_truncated
    )


async def _scan_tasks(tracer: TracerApiClient) -> tuple[list[CleanupTaskSnapshot], bool]:
    """보관도 감춤도 되지 않은 태스크를 상한까지 여러 장에 걸쳐 읽는다."""
    scanned: list[CleanupTaskSnapshot] = []
    cursor: str | None = None
    while len(scanned) <= TASK_SCAN_LIMIT:
        payload = await tracer.get(
            TASKS_PATH, {"archived": "false", "limit": TASK_PAGE_LIMIT, "cursor": cursor}
        )
        items = list((payload or {}).get("items") or [])
        scanned.extend(_snapshot(item) for item in items if item.get("origin") != SERVER_SDK_TASK_ORIGIN)
        next_cursor = (payload or {}).get("nextCursor")
        if not items or next_cursor is None:
            return scanned[:TASK_SCAN_LIMIT], len(scanned) > TASK_SCAN_LIMIT
        cursor = str(next_cursor)
    return scanned[:TASK_SCAN_LIMIT], True


async def _active_child_counts(tracer: TracerApiClient, task_ids: list[str]) -> dict[str, int]:
    """후보마다 아직 도는 자식이 몇인지 센다."""
    counts: dict[str, int] = {}
    for task_id in task_ids:
        payload = await tracer.get(f"{TASKS_PATH}/{task_id}/children")
        if payload is None:
            continue
        active = sum(1 for child in payload.get("items") or [] if child.get("status") in _ACTIVE_STATUSES)
        if active:
            counts[task_id] = active
    return counts


def _snapshot(item: dict[str, Any]) -> CleanupTaskSnapshot:
    last_event_at = item.get("lastEventAt")
    return {
        "id": item["id"],
        "title": item["title"],
        "status": item["status"],
        "lastEventAt": None if last_event_at is None else _instant(last_event_at),
        "updatedAt": _instant(item["updatedAt"]),
    }


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
