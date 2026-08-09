"""사용자 장기기억의 조회와 즉시 적재를 계약이 정한 경로로 받는다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...runtime.dependencies import ExecutionSql, UserId
from ...shared.ids import generate_ulid
from ...shared.wire import SuccessEnvelope, error_envelope, error_responses, ok
from ..memory_policy import memory_rejection
from .envelope import read_payload
from .ledger import ChatSurfaceLedger
from .models import RememberFactBody
from .views import memory_dto

CHAT_MEMORIES_PATH = "/api/agent/chat/memories"
CHAT_MEMORY_PATH = f"{CHAT_MEMORIES_PATH}/{{key}}"

REMEMBERED = "remembered"

# 저장된 문장은 다음 턴의 문맥으로 되돌아오므로 실을 수 없는 내용은 계약이 정한 거절로 낸다.
MEMORY_REJECTED = (400, "validation_error", "Invalid request")

router = APIRouter()


@router.get(CHAT_MEMORIES_PATH, response_model=SuccessEnvelope)
async def recall_chat_facts(source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """이 사용자의 장기기억 전체를 최근 갱신순으로 낸다."""
    async with source.connect() as sql:
        rows = await ChatSurfaceLedger(sql).list_memories(user_id)
    return ok({"facts": [memory_dto(row) for row in rows]})


@router.put(CHAT_MEMORY_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def remember_chat_fact(
    key: str, request: Request, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """사실 하나를 확인 대기 없이 같은 키 자리에 즉시 적는다."""
    body = await read_payload(request, RememberFactBody)
    if isinstance(body, JSONResponse):
        return body
    refused = memory_rejection(body.content)
    if refused is not None:
        return error_envelope(*MEMORY_REJECTED, details=[{"loc": ["content"], "type": refused}])
    now = datetime.now(UTC)
    async with source.connect() as sql:
        await ChatSurfaceLedger(sql).upsert_memory(generate_ulid(now), user_id, key, body.content, now)
    return ok({"key": key, "content": body.content, "status": REMEMBERED})
