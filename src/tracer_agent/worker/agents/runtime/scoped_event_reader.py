"""사용자 범위가 묶인 공통 이벤트 원장 조회를 제공한다."""

from __future__ import annotations

from typing import Any, Literal

from tracer_agent.shared.agents.runtime.ledger import LedgerPoolProvider

_TASK_OWNED = "SELECT 1 FROM agent_task_view WHERE id = $1 AND user_id = $2"
_EVENT_COLUMNS = "id, seq, kind, title, body, tool_name, file_paths, occurred_at"
_EVENTS_ASC = f"""
    SELECT {_EVENT_COLUMNS} FROM agent_event_view
    WHERE task_id = $1 AND user_id = $2 AND ($3::bigint IS NULL OR seq > $3::bigint)
    ORDER BY seq ASC LIMIT $4
"""
_EVENTS_DESC = f"""
    SELECT {_EVENT_COLUMNS} FROM agent_event_view
    WHERE task_id = $1 AND user_id = $2 AND ($3::bigint IS NULL OR seq < $3::bigint)
    ORDER BY seq DESC LIMIT $4
"""
_EVENT_COUNT = "SELECT count(*) FROM agent_event_view WHERE task_id = $1 AND user_id = $2"


def _event_view(row: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": row["id"],
        "seq": str(row["seq"]),
        "kind": row["kind"],
        "title": row["title"],
        "filePaths": list(row["file_paths"] or []),
        "occurredAt": row["occurred_at"].isoformat().replace("+00:00", "Z"),
    }
    if row["body"] is not None:
        event["body"] = row["body"]
    if row["tool_name"] is not None:
        event["toolName"] = row["tool_name"]
    return event


class ScopedEventReader:
    """한 사용자가 소유한 태스크의 이벤트만 페이지 단위로 읽는다."""

    def __init__(self, ledger: LedgerPoolProvider, user_id: str) -> None:
        self._ledger = ledger
        self._user_id = user_id

    async def task_events(
        self, task_id: str, limit: int, cursor: str | None, order: Literal["asc", "desc"]
    ) -> dict[str, Any] | None:
        """태스크 이벤트 한 페이지를 읽으며 소유하지 않은 태스크에는 아무것도 돌려주지 않는다."""
        pool = await self._ledger.pool()
        async with pool.acquire() as connection:
            owned = await connection.fetchval(_TASK_OWNED, task_id, self._user_id)
            if owned is None:
                return None
            boundary = int(cursor) if cursor is not None else None
            statement = _EVENTS_DESC if order == "desc" else _EVENTS_ASC
            rows = await connection.fetch(statement, task_id, self._user_id, boundary, limit + 1)
            total = await connection.fetchval(_EVENT_COUNT, task_id, self._user_id)

        truncated = len(rows) > limit
        page = [_event_view(row) for row in rows[:limit]]
        result: dict[str, Any] = {"events": page, "truncated": truncated, "total": int(total)}
        if truncated and page:
            result["nextCursor"] = page[-1]["seq"]
        return result
