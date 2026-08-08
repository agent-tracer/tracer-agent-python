"""턴의 단계 하나를 실행하며 진입·완료·실패를 궤적에 남기고 그 단계가 요구한 재시도와 상한을 건다."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from langgraph.errors import NodeTimeoutError

from ...runtime.execution.trace import ExecutionTrace

# 다시 걸 수 있는 실패는 기다리는 간격을 늘려 가며 걸어 상류가 회복할 자리를 준다.
_FIRST_BACKOFF_S = 0.5
_BACKOFF_FACTOR = 2.0


class ChatTurnSteps:
    """한 턴의 단계마다 진입·완료·실패를 실행 궤적에 남기고 그 단계의 재시도와 상한을 소유한다."""

    def __init__(self, agent_name: str, trace: ExecutionTrace) -> None:
        self._agent_name = agent_name
        self._trace = trace

    async def run[ResultT](
        self,
        name: str,
        step: Callable[[], Awaitable[ResultT]],
        *,
        attempts: int = 1,
        retry_on: tuple[type[Exception], ...] = (),
        timeout_s: float | None = None,
    ) -> ResultT:
        """단계 하나를 실행하며 다시 걸 수 있는 실패는 상한에 닿을 때까지 다시 건다."""
        for attempt in range(1, attempts):
            try:
                return await self._once(name, step, timeout_s)
            except retry_on:
                await asyncio.sleep(_FIRST_BACKOFF_S * _BACKOFF_FACTOR ** (attempt - 1))
        return await self._once(name, step, timeout_s)

    async def _once[ResultT](
        self, name: str, step: Callable[[], Awaitable[ResultT]], timeout_s: float | None
    ) -> ResultT:
        self._trace.record_orchestration_event(
            "node.started", f"{self._agent_name} entered {name}", node_name=name
        )
        started = time.monotonic()
        try:
            result = await self._bounded(name, step, timeout_s)
        except BaseException:
            self._trace.record_orchestration_event(
                "node.failed",
                f"{self._agent_name} failed in {name}",
                node_name=name,
                duration_ms=_elapsed_ms(started),
            )
            raise
        self._trace.record_orchestration_event(
            "node.completed",
            f"{self._agent_name} completed {name}",
            node_name=name,
            duration_ms=_elapsed_ms(started),
        )
        return result

    @staticmethod
    async def _bounded[ResultT](
        name: str, step: Callable[[], Awaitable[ResultT]], timeout_s: float | None
    ) -> ResultT:
        if timeout_s is None:
            return await step()
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout_s) as bound:
                return await step()
        except TimeoutError as expired:
            # 단계 하나의 벽시계 초과는 워커가 그래프에서 받던 것과 같은 자리로 남아야 분류가 갈리지 않는다.
            if not bound.expired():
                raise
            raise NodeTimeoutError(
                name, time.monotonic() - started, kind="run", run_timeout=timeout_s
            ) from expired


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
