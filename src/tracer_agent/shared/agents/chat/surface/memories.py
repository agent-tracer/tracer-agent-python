"""사용자 장기기억의 조회와 즉시 적재를 계약이 정한 경로로 받는다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from ...runtime.ledger import SqlSource
from ..intake.ids import generate_ulid
from ..intake.router import MONITOR_USER_HEADER, resolve_user_id
from .envelope import ok, read_payload
from .ledger import ChatSurfaceLedger
from .models import RememberFactBody
from .views import memory_dto

CHAT_MEMORIES_PATH = "/api/agent/chat/memories"
CHAT_MEMORY_PATH = f"{CHAT_MEMORIES_PATH}/{{key}}"

REMEMBERED = "remembered"


async def recall_chat_facts(request: Request) -> JSONResponse:
    """이 사용자의 장기기억 전체를 최근 갱신순으로 낸다."""
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        rows = await ChatSurfaceLedger(sql).list_memories(user_id)
    return ok({"facts": [memory_dto(row) for row in rows]})


async def remember_chat_fact(key: str, request: Request) -> JSONResponse:
    """사실 하나를 확인 대기 없이 같은 키 자리에 즉시 적는다."""
    body = await read_payload(request, RememberFactBody)
    if isinstance(body, JSONResponse):
        return body
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    now = datetime.now(UTC)
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        await ChatSurfaceLedger(sql).upsert_memory(generate_ulid(now), user_id, key, body.content, now)
    return ok({"key": key, "content": body.content, "status": REMEMBERED})
