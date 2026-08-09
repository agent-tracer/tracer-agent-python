"""실행 도중 누적한 어시스턴트 답변을 원장에 적고 열린 연결에 알린다."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tracer_agent.shared.agents.chat.execution_ledger import ChatExecutionLedger
from tracer_agent.shared.agents.chat.models import (
    TERMINAL_CHAT_EXECUTION_STATUSES,
    ChatExecutionPhase,
)
from tracer_agent.shared.agents.chat.surface.contract import chat_draft_rules
from tracer_agent.shared.agents.runtime.ledger import SqlSource
from tracer_agent.shared.agents.runtime.wakeup import UpdatePublisher
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact_text

_log = logging.getLogger(__name__)


class ChatExecutionClosed(Exception):
    """서버가 실행을 종결해 더 실행할 이유가 없음을 알린다."""


class DraftSink(Protocol):
    """진행 중인 답변을 받는 자리이며 창구가 없어도 흐름이 한 갈래로 남게 한다."""

    async def push(self, text: str) -> None:
        """어시스턴트 조각을 누적한다."""
        ...

    async def push_tool(self, tool_name: str) -> None:
        """도구 호출을 누적분에 남긴다."""
        ...

    async def mark_phase(self, phase: ChatExecutionPhase) -> None:
        """실행이 무엇을 하는 중인지를 옮긴다."""
        ...

    async def flush(self) -> None:
        """아직 보내지 않은 누적분을 마지막으로 보낸다."""
        ...


class NullDraftPublisher:
    """창구를 받지 못한 실행에서도 토큰은 흐르되 보낼 곳이 없음을 나타낸다."""

    async def push(self, text: str) -> None:
        """받은 조각을 버린다."""

    async def push_tool(self, tool_name: str) -> None:
        """받은 도구 호출을 버린다."""

    async def mark_phase(self, phase: ChatExecutionPhase) -> None:
        """받은 구간을 버린다."""

    async def flush(self) -> None:
        """보낼 곳이 없으므로 아무것도 하지 않는다."""


def tool_marker(tool_name: str) -> str:
    """진행 표시에 남는 도구 한 줄이며 두 백엔드가 같은 모양을 쓴다."""
    return f"\n\n`{tool_name}`\n\n"


@dataclass(frozen=True)
class DraftAck:
    """이 조각이 원장에 남았는지와, 그 실행이 이미 종결되었는지다."""

    stored: bool
    terminal: bool


class DraftDelivery(Protocol):
    """누적분 하나를 원장에 옮기는 자리이며 리듬과 순번은 부르는 쪽이 소유한다."""

    async def __call__(self, seq: int, phase: ChatExecutionPhase, text: str) -> DraftAck: ...


class DraftPublisher:
    """누적 답변을 간격으로 묶어 원장에 적으며, 실패해도 실행을 멈추지 않는다."""

    def __init__(
        self,
        deliver: DraftDelivery,
        elapsed: Callable[[], float] = time.monotonic,
    ) -> None:
        self._deliver = deliver
        self._elapsed = elapsed
        self._text = ""
        self._seq = 0
        self._sent_at = 0.0
        self._opened = False
        self._pending = False
        self._closed = False
        self._phase: ChatExecutionPhase = "starting"
        self._tail: asyncio.Future[None] | None = None

    async def push(self, text: str) -> None:
        """어시스턴트 조각을 누적하고 간격이 지났으면 지금까지의 전문을 보낸다."""
        if not text:
            return
        self._raise_if_closed()
        self._text += text
        self._seq += 1
        self._phase = "responding"
        self._pending = True
        if not self._opened or self._elapsed() - self._sent_at >= chat_draft_rules().interval_s:
            self._opened = True
            self._enqueue()

    async def push_tool(self, tool_name: str) -> None:
        """도구가 실행되는 동안에도 진행이 보이도록 호출을 누적분에 남긴다."""
        self._raise_if_closed()
        self._text += tool_marker(tool_name)
        self._seq += 1
        self._phase = "tool"
        self._pending = True
        self._enqueue()

    async def mark_phase(self, phase: ChatExecutionPhase) -> None:
        """글자를 내지 않는 구간에도 무엇을 하는 중인지를 옮겨 화면이 멈추지 않게 한다."""
        self._raise_if_closed()
        if phase == self._phase:
            return
        # 답변이 흐르는 중에는 사고로 되돌리지 않아야 표시가 조각마다 흔들리지 않는다.
        if phase == "thinking" and self._phase == "responding":
            return
        self._phase = phase
        self._seq += 1
        self._pending = True
        self._enqueue()

    async def flush(self) -> None:
        """아직 보내지 않은 누적분을 보내고 앞서 보낸 것들이 끝날 때까지 기다린다."""
        if self._pending:
            self._enqueue()
        tail = self._tail
        if tail is not None:
            await asyncio.shield(tail)
        self._raise_if_closed()

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise ChatExecutionClosed("chat execution is already terminal")

    def _enqueue(self) -> None:
        """모델이 토큰을 소비하는 루프를 막지 않도록 전송을 뒤로 미루고 순서만 지킨다."""
        self._pending = False
        self._sent_at = self._elapsed()
        # 누적 전문을 가리므로 자격이 조각 경계에 걸쳐 있어도 온전한 모양으로 걸린다.
        text = redact_text(self._text, stage=RedactionStage.OUTPUT)
        previous = self._tail
        self._tail = asyncio.ensure_future(self._after(previous, self._seq, self._phase, text))

    async def _after(
        self,
        previous: asyncio.Future[None] | None,
        seq: int,
        phase: ChatExecutionPhase,
        text: str,
    ) -> None:
        if previous is not None:
            with contextlib.suppress(Exception):
                await previous
        await self._send(seq, phase, text)

    async def _send(self, seq: int, phase: ChatExecutionPhase, text: str) -> None:
        try:
            ack = await self._deliver(seq, phase, text)
        except Exception as err:
            # draft는 정본이 아니라 진행 표시라, 못 적어도 최종 결과 배달을 막지 않는다.
            _log.warning("chat draft checkpoint failed: %s", err)
            return
        if ack.terminal:
            # 원장이 이미 종결이라 다음 조각에서 실행을 접는다.
            self._closed = True


@dataclass(frozen=True)
class LedgerDraftDelivery:
    """누적분을 원장에 적고 다른 프로세스의 열린 연결에 브로커로 알린다."""

    sql: SqlSource
    execution_id: str
    attempt: int
    wakeup: UpdatePublisher | None = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def __call__(self, seq: int, phase: ChatExecutionPhase, text: str) -> DraftAck:
        # 모델 루프가 분 단위로 실행하는 동안 연결을 쥐지 않도록 조각마다 빌리고 놓는다.
        async with self.sql.connect() as sql:
            ledger = ChatExecutionLedger(sql)
            stored = await ledger.checkpoint_running(
                self.execution_id, self.attempt, text, seq, phase, self.now()
            )
            row = None if stored else await ledger.find_by_id(self.execution_id)
        if stored and self.wakeup is not None:
            await self.wakeup.publish(self.execution_id, {"executionId": self.execution_id})
        # 앞선 순번이라 안 적혔을 수도 있으므로 종결은 원장의 상태로만 읽는다.
        terminal = row is not None and str(row["status"]) in TERMINAL_CHAT_EXECUTION_STATUSES
        return DraftAck(stored=stored, terminal=terminal)
