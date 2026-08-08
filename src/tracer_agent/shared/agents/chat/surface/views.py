"""대화 원장 행을 계약이 정한 와이어 표현으로 바꾼다."""

from __future__ import annotations

from ...runtime.ledger import SqlRow
from ...shared.instant import opt_iso
from ...shared.json_view import JsonObject
from ...shared.step_view import step_row_view

__all__ = [
    "confirmation_dto",
    "execution_dto",
    "memory_dto",
    "message_dto",
    "step_dto",
    "thread_dto",
]


def thread_dto(row: SqlRow) -> JsonObject:
    """스레드 한 행을 목록과 상세와 생성과 개명이 함께 내는 표현으로 바꾼다."""
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "title": row["title"],
        "summary": row["summary"],
        "backend": row["backend"],
        "createdAt": opt_iso(row["created_at"]),
        "updatedAt": opt_iso(row["updated_at"]),
    }


def step_dto(row: SqlRow) -> JsonObject:
    """궤적 한 줄을 값이 있는 자리만 실은 표현으로 바꾼다."""
    return step_row_view(row)


def message_dto(row: SqlRow) -> JsonObject:
    """저장된 사용자 메시지 행을 계약이 정한 와이어 표현으로 바꾼다."""
    return {
        "id": row["id"],
        "threadId": row["thread_id"],
        "role": row["role"],
        "content": row["content"],
        "toolCalls": row["tool_calls"],
        "toolCallId": row["tool_call_id"],
        "createdAt": opt_iso(row["created_at"]),
    }


def execution_dto(row: SqlRow) -> JsonObject:
    """저장된 실행 행을 계약이 정한 와이어 표현으로 바꾼다."""
    return {
        "id": row["id"],
        "threadId": row["thread_id"],
        "replayAnchorMessageId": row["replay_anchor_message_id"],
        "status": row["status"],
        "phase": row["phase"],
        "requestedBackend": row["requested_backend"],
        "draftText": row["draft_text"],
        "draftSeq": row["draft_seq"],
        "assistantMessageId": row["assistant_message_id"],
        "modelUsed": row["model_used"],
        "costUsd": row["cost_usd"],
        "numTurns": row["num_turns"],
        "stopReason": row["stop_reason"],
        "error": row["error"],
        "createdAt": opt_iso(row["created_at"]),
        "updatedAt": opt_iso(row["updated_at"]),
        "startedAt": opt_iso(row["started_at"]),
        "completedAt": opt_iso(row["completed_at"]),
    }


def confirmation_dto(row: SqlRow) -> JsonObject:
    """승인을 기다리는 도구 호출 한 행을 표현으로 바꾼다."""
    return {"id": row["id"], "toolName": row["tool_name"], "args": row["args"] or {}}


def memory_dto(row: SqlRow) -> JsonObject:
    """사용자 장기기억 한 줄을 표현으로 바꾼다."""
    return {"key": row["key"], "content": row["content"], "updatedAt": opt_iso(row["updated_at"])}
