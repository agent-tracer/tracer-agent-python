"""대화 스레드와 그 메시지의 조회와 개설과 개명과 삭제를 계약이 정한 경로로 받는다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from ...runtime.ledger import LedgerSql, SqlSource
from ..execution_ledger import ChatExecutionLedger
from ..intake.dispatch import ExecutionDispatch
from ..intake.ids import generate_ulid
from ..intake.router import CHAT_THREADS_PATH, MONITOR_USER_HEADER, resolve_user_id
from ..intake.turn import ChatIntakeRejected
from ..models import TERMINAL_CHAT_EXECUTION_STATUSES
from .access import owned_thread
from .envelope import CREATED_STATUS, ok, read_payload, rejection
from .ledger import ChatSurfaceLedger
from .models import ThreadTitleBody
from .views import message_dto, thread_dto

CHAT_THREAD_PATH = f"{CHAT_THREADS_PATH}/{{thread_id}}"
CHAT_THREAD_MESSAGES_PATH = f"{CHAT_THREAD_PATH}/messages"


async def list_chat_threads(request: Request) -> JSONResponse:
    """이 사용자의 대화 스레드를 최근 갱신순으로 낸다."""
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    async with _source(request).connect() as sql:
        rows = await ChatSurfaceLedger(sql).list_threads(user_id)
    return ok({"items": [thread_dto(row) for row in rows]})


async def create_chat_thread(request: Request) -> JSONResponse:
    """새 대화 스레드를 연다."""
    body = await read_payload(request, ThreadTitleBody)
    if isinstance(body, JSONResponse):
        return body
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    now = datetime.now(UTC)
    async with _source(request).connect() as sql:
        row = await ChatSurfaceLedger(sql).insert_thread(generate_ulid(now), user_id, body.title, now)
    return ok({"thread": thread_dto(row)}, status=CREATED_STATUS)


async def get_chat_thread(thread_id: str, request: Request) -> JSONResponse:
    """대화 스레드 하나를 낸다."""
    try:
        async with _source(request).connect() as sql:
            thread = await owned_thread(ChatSurfaceLedger(sql), _user(request), thread_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    return ok({"thread": thread_dto(thread)})


async def rename_chat_thread(thread_id: str, request: Request) -> JSONResponse:
    """대화 스레드의 제목을 고친다."""
    body = await read_payload(request, ThreadTitleBody)
    if isinstance(body, JSONResponse):
        return body
    try:
        async with _source(request).connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, _user(request), thread_id)
            row = await ledger.rename_thread(thread_id, body.title, datetime.now(UTC))
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    return ok({"thread": thread_dto(row)})


async def delete_chat_thread(thread_id: str, request: Request) -> JSONResponse:
    """대화 스레드를 그 메시지와 실행과 대기 도구까지 지운다."""
    dispatch: ExecutionDispatch = request.app.state.execution_dispatch
    try:
        async with _source(request).connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, _user(request), thread_id)
            active = await _close_active(sql, ledger, thread_id)
            # 사용자 장기기억은 스레드가 아니라 사용자에 매인 것이라 함께 지우지 않는다.
            await ledger.delete_thread(thread_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    for execution_id in active:
        await dispatch.cancel(execution_id)
    return ok({"deleted": True})


async def list_chat_messages(thread_id: str, request: Request) -> JSONResponse:
    """스레드에 쌓인 메시지를 쌓인 순서대로 낸다."""
    try:
        async with _source(request).connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, _user(request), thread_id)
            rows = await ledger.list_messages(thread_id)
    except ChatIntakeRejected as rejected:
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


def _source(request: Request) -> SqlSource:
    source: SqlSource = request.app.state.execution_sql
    return source


def _user(request: Request) -> str:
    return resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
