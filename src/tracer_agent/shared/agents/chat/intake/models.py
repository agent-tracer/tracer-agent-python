"""브라우저가 치는 접수 요청과 그 응답의 와이어 계약이다."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from ...runtime.ledger import SqlRow
from ...shared.instant import opt_iso
from ...shared.models import Language, TrimmedStr

ModelName = Annotated[TrimmedStr, Field(min_length=1)]


class PostMessagePayload(BaseModel):
    """브라우저가 보내는 접수 본문이며 계약이 정한 제약을 건다."""

    model_config = ConfigDict(extra="ignore")

    clientRequestId: TrimmedStr = Field(min_length=1, max_length=200)
    content: TrimmedStr = Field(min_length=1, max_length=10_000)
    model: ModelName | None = None
    language: Language | None = None

    def input_hash(self) -> str:
        """같은 요청 식별자가 같은 입력인지 가릴 해시를 두 구현체가 같은 바이트로 만든다."""
        payload = {
            "content": self.content,
            "model": self.model,
            "language": self.language,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def message_dto(row: SqlRow) -> dict[str, Any]:
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


def execution_dto(row: SqlRow) -> dict[str, Any]:
    """저장된 실행 행을 계약이 정한 와이어 표현으로 바꾼다."""
    return {
        "id": row["id"],
        "threadId": row["thread_id"],
        "userMessageId": row["user_message_id"],
        "status": row["status"],
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
