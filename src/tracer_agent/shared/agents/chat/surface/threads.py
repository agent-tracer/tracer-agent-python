"""대화 스레드와 그 메시지의 조회와 개설과 개명과 삭제를 계약이 정한 경로로 받는다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...runtime.dependencies import ExecutionSql, UserId
from ...runtime.ledger import LedgerSql
from ...shared.ids import generate_ulid
from ...shared.wire import SuccessEnvelope, error_responses, ok
from ..dependencies import Dispatch
from ..execution_ledger import ChatExecutionLedger
from ..intake.router import CHAT_THREADS_PATH
from ..models import TERMINAL_CHAT_EXECUTION_STATUSES
from ..rejections import ChatRejected
from .access import owned_thread
from .envelope import CREATED_STATUS, read_payload, rejection
from .ledger import ChatSurfaceLedger
from .models import ThreadTitleBody
from .views import message_dto, thread_dto

CHAT_THREAD_PATH = f"{CHAT_THREADS_PATH}/{{thread_id}}"
CHAT_THREAD_MESSAGES_PATH = f"{CHAT_THREAD_PATH}/messages"

router = APIRouter()


@router.get(CHAT_THREADS_PATH, response_model=SuccessEnvelope)
async def list_chat_threads(source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """이 사용자의 대화 스레드를 최근 갱신순으로 낸다."""
    async with source.connect() as sql:
        rows = await ChatSurfaceLedger(sql).list_threads(user_id)
    return ok({"items": [thread_dto(row) for row in rows]})


@router.post(
    CHAT_THREADS_PATH,
    status_code=CREATED_STATUS,
    response_model=SuccessEnvelope,
    responses=error_responses(400),
)
async def create_chat_thread(request: Request, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """새 대화 스레드를 연다."""
    body = await read_payload(request, ThreadTitleBody)
    if isinstance(body, JSONResponse):
        return body
    now = datetime.now(UTC)
    async with source.connect() as sql:
        row = await ChatSurfaceLedger(sql).insert_thread(generate_ulid(now), user_id, body.title, now)
    return ok({"thread": thread_dto(row)}, status=CREATED_STATUS)


@router.get(CHAT_THREAD_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def get_chat_thread(thread_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """대화 스레드 하나를 낸다."""
    try:
        async with source.connect() as sql:
            thread = await owned_thread(ChatSurfaceLedger(sql), user_id, thread_id)
    except ChatRejected as rejected:
        return rejection(rejected)
    return ok({"thread": thread_dto(thread)})


@router.patch(CHAT_THREAD_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404))
async def rename_chat_thread(
    thread_id: str, request: Request, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """대화 스레드의 제목을 고친다."""
    body = await read_payload(request, ThreadTitleBody)
    if isinstance(body, JSONResponse):
        return body
    try:
        async with source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, user_id, thread_id)
            row = await ledger.rename_thread(thread_id, body.title, datetime.now(UTC))
    except ChatRejected as rejected:
        return rejection(rejected)
    return ok({"thread": thread_dto(row)})


@router.delete(CHAT_THREAD_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def delete_chat_thread(
    thread_id: str, source: ExecutionSql, user_id: UserId, dispatch: Dispatch
) -> JSONResponse:
    """대화 스레드를 그 메시지와 실행과 대기 도구까지 지운다."""
    try:
        async with source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, user_id, thread_id)
            active = await _close_active(sql, ledger, thread_id)
            await ledger.delete_thread(thread_id)
    except ChatRejected as rejected:
        return rejection(rejected)
    for execution_id in active:
        await dispatch.cancel(execution_id)
    return ok({"deleted": True})


@router.get(CHAT_THREAD_MESSAGES_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def list_chat_messages(thread_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """스레드에 쌓인 메시지를 쌓인 순서대로 낸다."""
    try:
        async with source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, user_id, thread_id)
            rows = await ledger.list_messages(thread_id)
    except ChatRejected as rejected:
        return rejection(rejected)
    return ok({"items": [message_dto(row) for row in rows]})


async def _close_active(sql: LedgerSql, ledger: ChatSurfaceLedger, thread_id: str) -> list[str]:
    executions = await ledger.list_executions(thread_id)
    active = [str(row["id"]) for row in executions if row["status"] not in TERMINAL_CHAT_EXECUTION_STATUSES]
    closing = ChatExecutionLedger(sql)
    now = datetime.now(UTC)
    for execution_id in active:
        await closing.cancel_active(execution_id, now)
    return active
