"""task-cleanup의 사용자 범위 이벤트 조회와, 접수가 직접 받은 요청의 후보 배치 조립을 제공한다."""

from __future__ import annotations

from datetime import datetime

from tracer_agent.shared.agents.runtime.ledger import LedgerPoolProvider
from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch

from ..runtime.scoped_event_reader import ScopedEventReader
from .policy import CleanupTaskSnapshot, qualify_candidates

# 조회 로직이 title-suggestion과 완전히 같아 새 서브클래스 대신 이름만 이 슬라이스로 가져온다.
CleanupLedgerReader = ScopedEventReader

# SDK 축의 TASK_SCAN_LIMIT과 값을 맞춘 상한이다.
TASK_SCAN_LIMIT = 500
SERVER_SDK_TASK_ORIGIN = "server-sdk"
_RUNNING_STATUS = "running"
_WAITING_STATUS = "waiting"

_TASKS = """
    SELECT id, title, status, last_event_at, updated_at, parent_task_id
    FROM agent_task_view
    WHERE user_id = $1 AND origin != $2 AND archived_at IS NULL AND hidden_at IS NULL
    ORDER BY id
    LIMIT $3
"""

_ACTIVE_CHILDREN = """
    SELECT parent_task_id FROM agent_task_view
    WHERE user_id = $1 AND parent_task_id = ANY($2::text[]) AND status = ANY($3::text[])
"""


async def load_cleanup_batch(ledger: LedgerPoolProvider, user_id: str, now: datetime) -> CleanupBatch:
    """SDK 축의 loadScanBatch와 같은 규칙으로 후보 배치를 원장 뷰에서 조립한다."""
    pool = await ledger.pool()
    async with pool.acquire() as connection:
        rows = await connection.fetch(_TASKS, user_id, SERVER_SDK_TASK_ORIGIN, TASK_SCAN_LIMIT + 1)
        batch_truncated = len(rows) > TASK_SCAN_LIMIT
        rows = rows[:TASK_SCAN_LIMIT] if batch_truncated else rows
        task_ids = [row["id"] for row in rows]
        active_child_rows = (
            await connection.fetch(_ACTIVE_CHILDREN, user_id, task_ids, [_RUNNING_STATUS, _WAITING_STATUS])
            if task_ids
            else []
        )

    active_child_counts: dict[str, int] = {}
    for row in active_child_rows:
        parent_id = row["parent_task_id"]
        active_child_counts[parent_id] = active_child_counts.get(parent_id, 0) + 1

    tasks: list[CleanupTaskSnapshot] = [
        {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "lastEventAt": row["last_event_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
    return CleanupBatch(
        candidates=qualify_candidates(tasks, active_child_counts, now), batchTruncated=batch_truncated
    )
