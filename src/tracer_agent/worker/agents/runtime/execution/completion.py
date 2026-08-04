"""분리 실행의 결과를 워커가 발급한 완료 창구로 전달한다."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from tracer_agent.shared.agents.shared.models import AgentResponse, CompletionCallback

DELIVERY_ATTEMPTS = 3
DELIVERY_BACKOFF_S = 2.0

_log = logging.getLogger(__name__)


async def run_and_deliver(
    client: httpx.AsyncClient,
    callback: CompletionCallback | None,
    execute_request: Callable[[], Awaitable[AgentResponse]],
    on_result: Callable[[AgentResponse], Awaitable[None]] | None = None,
) -> None:
    """실행을 끝까지 수행하고 창구가 있으면 결과를 한 번 전달하며, 전달 전 후처리를 먼저 실행한다."""
    try:
        response = await execute_request()
    except asyncio.CancelledError:
        return
    if on_result is not None:
        await on_result(response)
    await deliver_completion(client, callback, response)


async def deliver_completion(
    client: httpx.AsyncClient,
    callback: CompletionCallback | None,
    response: AgentResponse,
) -> None:
    """창구가 있으면 실행 결과를 한 번 전달하고 없으면 아무 곳도 부르지 않는다."""
    if callback is not None:
        await _deliver(client, callback, response)


async def _deliver(
    client: httpx.AsyncClient,
    callback: CompletionCallback,
    response: AgentResponse,
) -> None:
    body = {"token": callback.token, "response": response.model_dump(mode="json")}
    for attempt in range(DELIVERY_ATTEMPTS):
        try:
            delivered = await client.post(callback.url, json=body)
            delivered.raise_for_status()
            return
        except httpx.HTTPError as error:
            if attempt == DELIVERY_ATTEMPTS - 1:
                # 워커는 자기 데드라인으로 실패를 판정하므로 여기서 더 보내지 않는다.
                _log.error("completion delivery failed: %s", error)
                return
            await asyncio.sleep(DELIVERY_BACKOFF_S**attempt)
