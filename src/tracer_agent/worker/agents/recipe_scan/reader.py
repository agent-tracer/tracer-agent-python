"""recipe-scan이 원장 뷰에서 태스크와 이벤트와 규칙을 읽는 사용자 범위 진입점을 소유한다."""

from __future__ import annotations

from typing import Any, Literal

from tracer_agent.shared.agents.runtime.ledger import LedgerPoolProvider

_TASK_OWNED = "SELECT 1 FROM agent_task_view WHERE id = $1 AND user_id = $2"

_TASK = """
    SELECT id, title, status, task_kind, workspace_path, created_at, updated_at
    FROM agent_task_view WHERE id = $1 AND user_id = $2
"""

_EVENT_COLUMNS = "id, seq, turn_id, kind, title, body, tool_name, file_paths, occurred_at"

_EVENTS_ASC = f"""
    SELECT {_EVENT_COLUMNS}, metadata FROM agent_event_view
    WHERE task_id = $1 AND user_id = $2 AND ($3::bigint IS NULL OR seq > $3::bigint)
    ORDER BY seq ASC LIMIT $4
"""

_EVENTS_DESC = f"""
    SELECT {_EVENT_COLUMNS}, metadata FROM agent_event_view
    WHERE task_id = $1 AND user_id = $2 AND ($3::bigint IS NULL OR seq < $3::bigint)
    ORDER BY seq DESC LIMIT $4
"""

_EVENT_COUNT = "SELECT count(*) FROM agent_event_view WHERE task_id = $1 AND user_id = $2"

_RULES = """
    SELECT id, name, expectation, task_id, anchor_event_id, source, severity, rationale,
           signature, created_at
    FROM agent_rule_view WHERE user_id = $1 AND task_id = $2
"""


def _iso(value: Any) -> str:
    return str(value.isoformat()).replace("+00:00", "Z")


def slim_event(row: Any) -> dict[str, Any]:
    """모델에게 내줄 이벤트 표현으로 줄이며 값이 없는 필드는 싣지 않는다."""
    event: dict[str, Any] = {
        "id": row["id"],
        "seq": str(row["seq"]),
        "kind": row["kind"],
        "title": row["title"],
        "filePaths": list(row["file_paths"] or []),
        "occurredAt": _iso(row["occurred_at"]),
    }
    for key, column in (("turnId", "turn_id"), ("body", "body"), ("toolName", "tool_name")):
        if row[column] is not None:
            event[key] = row[column]
    return event


class RecipeLedgerReader:
    """한 사용자의 원장 뷰만 읽도록 생성 시점에 범위가 묶인 조회 진입점이다."""

    def __init__(self, ledger: LedgerPoolProvider, user_id: str) -> None:
        self._ledger = ledger
        self._user_id = user_id

    async def task_with_events(self, task_id: str, window: int) -> dict[str, Any] | None:
        """요약을 만들 태스크와 앞쪽 이벤트 창과 전체 건수를 함께 읽는다."""
        pool = await self._ledger.pool()
        async with pool.acquire() as connection:
            task = await connection.fetchrow(_TASK, task_id, self._user_id)
            if task is None:
                return None
            found = dict(task)
            rows = await connection.fetch(_EVENTS_ASC, task_id, self._user_id, None, window)
            total = await connection.fetchval(_EVENT_COUNT, task_id, self._user_id)
        return {"task": found, "rows": [dict(row) for row in rows], "total": int(total)}

    async def task_events(
        self, task_id: str, limit: int, cursor: str | None, order: Literal["asc", "desc"]
    ) -> dict[str, Any] | None:
        """태스크 이벤트 한 페이지를 읽으며 소유하지 않은 태스크에는 아무것도 돌려주지 않는다."""
        pool = await self._ledger.pool()
        async with pool.acquire() as connection:
            owned = await connection.fetchval(_TASK_OWNED, task_id, self._user_id)
            if owned is None:
                return None
            after = int(cursor) if cursor is not None else None
            statement = _EVENTS_DESC if order == "desc" else _EVENTS_ASC
            rows = await connection.fetch(statement, task_id, self._user_id, after, limit + 1)
            total = await connection.fetchval(_EVENT_COUNT, task_id, self._user_id)

        truncated = len(rows) > limit
        page = [slim_event(row) for row in rows[:limit]]
        result: dict[str, Any] = {"events": page, "truncated": truncated, "total": int(total)}
        if truncated and page:
            result["nextCursor"] = page[-1]["seq"]
        return result

    async def applicable_rules(self, task_id: str) -> list[dict[str, Any]]:
        """태스크에 적용되는 살아 있는 규칙만 읽는다."""
        pool = await self._ledger.pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(_RULES, self._user_id, task_id)
        return [_slim_rule(row) for row in rows]


def _slim_rule(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "expect": _expect_view(row["expectation"] or {}),
        "taskId": row["task_id"],
        "anchorEventId": row["anchor_event_id"],
        "source": row["source"],
        "severity": row["severity"],
        "rationale": row["rationale"],
        "signature": row["signature"],
        "createdAt": _iso(row["created_at"]),
    }


def _expect_view(expectation: dict[str, Any]) -> dict[str, Any]:
    kind = expectation.get("kind")
    if kind == "command":
        return {"kind": kind, "commandMatches": expectation.get("commandMatches")}
    view: dict[str, Any] = {"kind": kind}
    if kind == "pattern":
        view["pattern"] = expectation.get("pattern")
        if expectation.get("tool") is not None:
            view["action"] = expectation["tool"]
        return view
    view["action"] = expectation.get("tool")
    return view
