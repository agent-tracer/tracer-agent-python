"""실행 하나의 스냅샷을 연결이 살아 있는 동안 이어서 전송한다."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from ...runtime.dependencies import ExecutionSql, UserId
from ...runtime.ledger import LedgerUnavailable, SqlSource
from ..dependencies import Watch
from ..models import TERMINAL_CHAT_EXECUTION_STATUSES
from ..rejections import ChatRejected
from .access import owned_execution, owned_thread
from .contract import chat_stream_rules
from .envelope import rejection
from .executions import CHAT_EXECUTION_PATH
from .ledger import PENDING, ChatSurfaceLedger
from .updates import ChatExecutionUpdates
from .views import confirmation_dto, execution_dto

CHAT_EXECUTION_EVENTS_PATH = f"{CHAT_EXECUTION_PATH}/events"

_log = logging.getLogger(__name__)

EVENT_STREAM_MEDIA_TYPE = "text/event-stream"
SNAPSHOT_EVENT = "snapshot"

router = APIRouter()


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


@router.get(CHAT_EXECUTION_EVENTS_PATH, response_model=None)
async def watch_chat_execution(
    thread_id: str, execution_id: str, source: ExecutionSql, user_id: UserId, watch: Watch
) -> StreamingResponse | JSONResponse:
    """요청이 실어 보낸 Last-Event-ID 와 무관하게 그 순간의 정본부터 이어서 내고 종결에서 닫는다."""
    try:
        # 이 조회는 남의 실행을 열지 않기 위한 것이며 첫 프레임은 구독을 놓은 뒤에 다시 읽는다.
        await _snapshot(source, user_id, thread_id, execution_id)
    except ChatRejected as rejected:
        return rejection(rejected)
    return StreamingResponse(
        frames(watch, source, user_id, thread_id, execution_id),
        media_type=EVENT_STREAM_MEDIA_TYPE,
        headers=dict(chat_stream_rules().headers),
    )


async def frames(
    watch: ChatExecutionUpdates | None,
    source: SqlSource,
    user_id: str,
    thread_id: str,
    execution_id: str,
) -> AsyncIterator[str]:
    """연결이 살아 있는 동안 정본 스냅샷을 이어 내고 종결이나 끊김에서 구독을 놓는다."""
    signal = asyncio.Event()
    unsubscribe = _listen(watch, execution_id, signal)
    last = ""
    try:
        while True:
            # 구독보다 먼저 읽은 정본은 그 사이의 갱신을 놓치므로 첫 프레임도 이 자리에서 읽는다.
            snapshot = await _snapshot(source, user_id, thread_id, execution_id)
            frame = snapshot.frame()
            # 정본이 그대로면 거르되 주기 재전송은 게이트웨이 유휴 타임아웃을 막으므로 남긴다.
            if frame != last or snapshot.is_terminal():
                last = frame
                yield frame
            if snapshot.is_terminal():
                return
            if not await _await_change(signal):
                last = ""
    except ChatRejected:
        # 조회하던 실행이 사라졌으면 더 실을 것이 없으므로 연결을 닫는다.
        return
    except LedgerUnavailable:
        # 헤더가 이미 나가 상태를 바꿀 수 없으므로 계약의 어휘 대신 관측에만 남기고 닫는다.
        _log.warning("chat.stream.failed", exc_info=True)
        return
    finally:
        unsubscribe()


def _listen(watch: ChatExecutionUpdates | None, execution_id: str, signal: asyncio.Event) -> Any:
    if watch is None:
        return lambda: None
    return watch.subscribe(execution_id, signal.set)


async def _await_change(signal: asyncio.Event) -> bool:
    """신호를 받아 깨었으면 참을, 주기가 지나 깨었으면 거짓을 낸다."""
    try:
        await asyncio.wait_for(signal.wait(), chat_stream_rules().resend_interval_s)
    except TimeoutError:
        return False
    signal.clear()
    return True


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
