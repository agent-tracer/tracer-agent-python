"""실행 도중 누적한 어시스턴트 답변을 서버의 실행 창구로 되돌려 보낸다."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from tracer_agent.shared.agents.chat.models import ChatExecutionPhase, DraftCallback
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact_text

_log = logging.getLogger(__name__)

# 서버가 정본에 저장하는 간격이며, 이보다 잦게 보내면 같은 스냅샷을 여러 번 쓰게 된다.
DRAFT_INTERVAL_S = 0.15
DRAFT_TIMEOUT_S = 5.0


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


class DraftPublisher:
    """누적 답변을 간격으로 묶어 실행 창구에 보내며, 실패해도 실행을 멈추지 않는다."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        callback: DraftCallback,
        elapsed: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._callback = callback
        self._elapsed = elapsed
        self._text = ""
        self._seq = 0
        self._sent_at = 0.0
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
        if self._elapsed() - self._sent_at >= DRAFT_INTERVAL_S:
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
        body = {
            "token": self._callback.token,
            "attempt": self._callback.attempt,
            "draftSeq": self._seq,
            "phase": self._phase,
            # 누적 전문을 가리므로 자격이 조각 경계에 걸쳐 있어도 온전한 모양으로 걸린다.
            "text": redact_text(self._text, stage=RedactionStage.OUTPUT),
        }
        previous = self._tail
        self._tail = asyncio.ensure_future(self._after(previous, body))

    async def _after(self, previous: asyncio.Future[None] | None, body: dict[str, Any]) -> None:
        if previous is not None:
            with contextlib.suppress(Exception):
                await previous
        await self._send(body)

    async def _send(self, body: dict[str, Any]) -> None:
        try:
            response = await self._client.post(self._callback.url, json=body, timeout=DRAFT_TIMEOUT_S)
            response.raise_for_status()
        except httpx.HTTPError as err:
            # draft는 정본이 아니라 진행 표시라, 못 보내도 최종 결과 배달을 막지 않는다.
            _log.warning("chat draft callback failed: %s", err)
            return
        if _terminal(response):
            # 창구가 종결을 알렸으므로 다음 조각에서 실행을 접는다.
            self._closed = True


def _terminal(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("terminal") is True
