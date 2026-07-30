"""대화 원장 행을 계약이 정한 와이어 표현으로 바꾼다."""

from __future__ import annotations

from typing import Any

from ...runtime.ledger import SqlRow
from ..intake.models import execution_dto, iso, message_dto

__all__ = [
    "confirmation_dto",
    "execution_dto",
    "iso",
    "memory_dto",
    "message_dto",
    "step_dto",
    "thread_dto",
]

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


def thread_dto(row: SqlRow) -> dict[str, Any]:
    """스레드 한 행을 목록과 상세와 생성과 개명이 함께 내는 표현으로 바꾼다."""
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "title": row["title"],
        "summary": row["summary"],
        "backend": row["backend"],
        "createdAt": iso(row["created_at"]),
        "updatedAt": iso(row["updated_at"]),
    }


def step_dto(row: SqlRow) -> dict[str, Any]:
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


def confirmation_dto(row: SqlRow) -> dict[str, Any]:
    """승인을 기다리는 도구 호출 한 행을 표현으로 바꾼다."""
    return {"id": row["id"], "toolName": row["tool_name"], "args": row["args"] or {}}


def memory_dto(row: SqlRow) -> dict[str, Any]:
    """사용자 장기기억 한 줄을 표현으로 바꾼다."""
    return {"key": row["key"], "content": row["content"], "updatedAt": iso(row["updated_at"])}
