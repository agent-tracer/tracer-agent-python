"""task-cleanup의 사용자 범위 이벤트 조회와, 접수가 직접 받은 요청의 후보 배치 조립을 제공한다."""

from __future__ import annotations

from datetime import datetime

from tracer_agent.shared.agents.shared.instant import parse_instant
from tracer_agent.shared.agents.shared.json_view import (
    JsonObject,
    as_object,
    as_objects,
    opt_text,
    text,
)
from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch, CleanupTaskStatus

from ..runtime.tracer_client import TracerApiPort
from .candidates import CleanupTaskSnapshot, qualify_candidates, without_active_children

# 한 번의 스캔이 조회하는 태스크의 상한이며 걸러내기 전의 원본을 센다.
TASK_SCAN_LIMIT = 500
SERVER_SDK_TASK_ORIGIN = "server-sdk"
TASKS_PATH = "/api/v1/tasks"
# 목록 창구가 한 장에 내주는 상한이며 배치 하나가 여러 장에 걸친다.
TASK_PAGE_LIMIT = 100


async def load_cleanup_batch(tracer: TracerApiPort, now: datetime) -> CleanupBatch:
    """정리 후보 판정에 들어가는 배치를 추적 창구의 목록에서 조립한다."""
    tasks, batch_truncated = await _scan_tasks(tracer)
    shortlisted = qualify_candidates(tasks, now)
    counts = await _active_child_counts(tracer, [candidate.id for candidate in shortlisted])
    return CleanupBatch(
        candidates=without_active_children(shortlisted, counts),
        batchTruncated=batch_truncated,
        tasksScanned=len(tasks),
    )


async def _scan_tasks(tracer: TracerApiPort) -> tuple[list[CleanupTaskSnapshot], bool]:
    """보관도 감춤도 되지 않은 태스크를 상한까지 여러 장에 걸쳐 읽는다."""
    # 상한은 조회하는 창의 크기이므로 걸러내기 전의 원본을 세고, 자른 뒤에 server-sdk를 뺀다.
    visible = await _read_pages(tracer, TASK_SCAN_LIMIT + 1)
    truncated = len(visible) > TASK_SCAN_LIMIT
    limited = visible[:TASK_SCAN_LIMIT] if truncated else visible
    tasks = [_snapshot(item) for item in limited if item.get("origin") != SERVER_SDK_TASK_ORIGIN]
    return tasks, truncated


async def _read_pages(tracer: TracerApiPort, cap: int) -> list[JsonObject]:
    """목록 창구를 여러 장에 걸쳐 상한까지 읽는다."""
    read: list[JsonObject] = []
    cursor: str | None = None
    while len(read) < cap:
        payload = await tracer.get(
            TASKS_PATH, {"archived": "false", "limit": TASK_PAGE_LIMIT, "cursor": cursor}
        )
        page = {} if payload is None else as_object(payload)
        items = as_objects(page.get("items"))
        read.extend(items)
        cursor = opt_text(page.get("nextCursor"))
        if not items or cursor is None:
            break
    return read[:cap]


async def _active_child_counts(tracer: TracerApiPort, task_ids: list[str]) -> dict[str, int]:
    """후보마다 아직 진행 중인 자식이 몇인지 센다."""
    counts: dict[str, int] = {}
    for task_id in task_ids:
        payload = await tracer.get(f"{TASKS_PATH}/{task_id}/children")
        if payload is None:
            continue
        children = as_objects(as_object(payload).get("items"))
        active = sum(1 for child in children if child.get("status") in tuple(CleanupTaskStatus))
        if active:
            counts[task_id] = active
    return counts


def _snapshot(item: JsonObject) -> CleanupTaskSnapshot:
    last_event_at = opt_text(item.get("lastEventAt"))
    return {
        "id": text(item["id"]),
        "title": text(item["title"]),
        "status": text(item["status"]),
        "lastEventAt": None if last_event_at is None else parse_instant(last_event_at),
        "updatedAt": parse_instant(text(item["updatedAt"])),
    }
