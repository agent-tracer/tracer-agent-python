"""추적 창구의 답을 이 워커의 어휘로 옮기는 자리를 검증한다(네트워크 없음)."""

from __future__ import annotations

import httpx
import pytest

from tracer_agent.worker.agents.runtime.tracer_client import (
    TRANSIENT_TRACER_ERRORS,
    TracerApiClient,
    TracerApiRejected,
    TracerApiUnavailable,
)

_PATH = "/api/v1/tasks/task-1/timeline"


def _client(answer: httpx.Response) -> TracerApiClient:
    transport = httpx.MockTransport(lambda _request: answer)
    return TracerApiClient(httpx.AsyncClient(transport=transport), "http://tracer", "user-1")


async def test_실은_것이_있는_봉투는_그대로_벗긴다() -> None:
    read = await _client(httpx.Response(200, json={"ok": True, "data": {"items": []}})).get(_PATH)

    assert read == {"items": []}


async def test_없는_태스크는_아무것도_돌려주지_않는다() -> None:
    assert await _client(httpx.Response(404)).get(_PATH) is None


async def test_비운_실은_것은_없음_그대로다() -> None:
    assert await _client(httpx.Response(200, json={"ok": True, "data": None})).get(_PATH) is None


async def test_실은_것이_없는_봉투는_없음이_아니라_일시_오류다() -> None:
    with pytest.raises(TracerApiUnavailable):
        await _client(httpx.Response(200, json={"ok": True})).get(_PATH)


async def test_실은_것이_없는_봉투는_다시_부를_수_있는_오류로_분류된다() -> None:
    with pytest.raises(TRANSIENT_TRACER_ERRORS):
        await _client(httpx.Response(200, json={"ok": True})).post(_PATH, {})


async def test_봉투가_아닌_답은_일시_오류다() -> None:
    with pytest.raises(TracerApiUnavailable):
        await _client(httpx.Response(200, json={"items": []})).get(_PATH)


async def test_거절은_같은_본문으로_다시_보내지_않는다() -> None:
    with pytest.raises(TracerApiRejected):
        await _client(httpx.Response(400, text="bad request")).get(_PATH)
