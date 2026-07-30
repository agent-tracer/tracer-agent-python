"""잡 원장 행과 궤적 행을 계약이 정한 와이어 표현으로 바꾼다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..agents.runtime.ledger import SqlRow

# 값이 없으면 궤적 한 줄에 싣지 않는 자리이며 열 이름과 wire 이름을 함께 든다.
_OPTIONAL_STEP_FIELDS: tuple[tuple[str, str], ...] = (
    ("tool_name", "toolName"),
    ("tool_call_id", "toolCallId"),
    ("input_tokens", "inputTokens"),
    ("output_tokens", "outputTokens"),
    ("cache_read_tokens", "cacheReadTokens"),
    ("cache_creation_tokens", "cacheCreationTokens"),
    ("stop_reason", "stopReason"),
    ("node_name", "nodeName"),
    ("event_kind", "eventKind"),
    ("duration_ms", "durationMs"),
)


def job_dto(row: SqlRow) -> dict[str, Any]:
    """잡 원장 행 하나를 접수와 조회와 취소가 함께 내는 표현으로 바꾼다."""
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "kind": row["kind"],
        "executor": row["executor"],
        "status": row["status"],
        "attempts": row["attempts"],
        "taskId": row["task_id"],
        "input": row["input"] or {},
        "result": row["result"] or {},
        "usage": row["usage"] or {},
        "error": row["error"],
        "createdAt": iso(row["created_at"]),
        "updatedAt": iso(row["updated_at"]),
        "startedAt": iso(row["started_at"]),
        "completedAt": iso(row["completed_at"]),
    }


def job_step_dto(row: SqlRow) -> dict[str, Any]:
    """궤적 한 줄을 값이 있는 자리만 실은 표현으로 바꾼다."""
    step: dict[str, Any] = {
        "seq": row["seq"],
        "attempt": row["attempt"],
        "role": row["role"],
        "content": row["content"],
        "truncated": bool(row["truncated"]),
        "toolCalls": row["tool_calls"] or [],
    }
    for column, name in _OPTIONAL_STEP_FIELDS:
        if row[column] is not None:
            step[name] = row[column]
    return step


def iso(value: datetime | None) -> str | None:
    """시각을 자바스크립트 Date가 내는 밀리초 세 자리 UTC 문자열로 적는다."""
    if value is None:
        return None
    moment = value.astimezone(UTC)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond // 1000:03d}Z"
