"""실행 하나의 스냅샷을 연결이 살아 있는 동안 이어서 흘려보낸다."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...runtime.ledger import SqlSource
from ..intake.router import MONITOR_USER_HEADER, resolve_user_id
from ..intake.turn import ChatIntakeRejected
from ..models import TERMINAL_CHAT_EXECUTION_STATUSES
from .access import owned_execution, owned_thread
from .contract import chat_stream_rules
from .envelope import rejection
from .executions import CHAT_EXECUTION_PATH
from .ledger import PENDING, ChatSurfaceLedger
from .updates import ChatExecutionUpdates
from .views import confirmation_dto, execution_dto

CHAT_EXECUTION_EVENTS_PATH = f"{CHAT_EXECUTION_PATH}/events"

EVENT_STREAM_MEDIA_TYPE = "text/event-stream"
SNAPSHOT_EVENT = "snapshot"


@dataclass(frozen=True)
class ChatExecutionSnapshot:
    """열린 연결이 한 프레임에 싣는 실행 상태와 대기 도구다."""

    execution: dict[str, Any]
    confirmations: list[dict[str, Any]]

    def frame(self) -> str:
        """프레임 하나를 사건 이름 줄과 본문 줄로 적는다."""
        body = json.dumps(
            {"execution": self.execution, "confirmations": self.confirmations}, ensure_ascii=False
        )
        return f"event: {SNAPSHOT_EVENT}\ndata: {body}\n\n"

    def is_terminal(self) -> bool:
        """이 스냅샷이 종결 상태를 실었는지 낸다."""
        return self.execution["status"] in TERMINAL_CHAT_EXECUTION_STATUSES


async def watch_chat_execution(
    thread_id: str, execution_id: str, request: Request
) -> StreamingResponse | JSONResponse:
    """요청이 실어 보낸 Last-Event-ID 와 무관하게 그 순간의 정본부터 이어서 내고 종결에서 닫는다."""
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    source: SqlSource = request.app.state.execution_sql
    try:
        first = await _snapshot(source, user_id, thread_id, execution_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    return StreamingResponse(
        _frames(request, source, user_id, thread_id, execution_id, first),
        media_type=EVENT_STREAM_MEDIA_TYPE,
        headers=dict(chat_stream_rules().headers),
    )


async def _frames(
    request: Request,
    source: SqlSource,
    user_id: str,
    thread_id: str,
    execution_id: str,
    first: ChatExecutionSnapshot,
) -> AsyncIterator[str]:
    signal = asyncio.Event()
    unsubscribe = _listen(request, execution_id, signal)
    snapshot = first
    try:
        while True:
            yield snapshot.frame()
            if snapshot.is_terminal():
                return
            await _await_change(signal)
            snapshot = await _snapshot(source, user_id, thread_id, execution_id)
    except ChatIntakeRejected:
        # 조회하던 실행이 사라졌으면 더 실을 것이 없으므로 연결을 닫는다.
        return
    finally:
        unsubscribe()


def _listen(request: Request, execution_id: str, signal: asyncio.Event) -> Any:
    updates: ChatExecutionUpdates | None = getattr(request.app.state, "execution_watch", None)
    if updates is None:
        return lambda: None
    return updates.subscribe(execution_id, signal.set)


async def _await_change(signal: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(signal.wait(), chat_stream_rules().resend_interval_s)
    except TimeoutError:
        return
    signal.clear()


async def _snapshot(
    source: SqlSource, user_id: str, thread_id: str, execution_id: str
) -> ChatExecutionSnapshot:
    async with source.connect() as sql:
        ledger = ChatSurfaceLedger(sql)
        await owned_thread(ledger, user_id, thread_id)
        execution = await owned_execution(ledger, user_id, thread_id, execution_id)
        pending = await ledger.list_pending_tools(thread_id)
    return ChatExecutionSnapshot(
        execution=execution_dto(execution),
        confirmations=[confirmation_dto(row) for row in pending if row["status"] == PENDING],
    )
