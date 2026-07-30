"""이 시도가 쓸 자격과 단가와 한도를 서버에서 받아 잡 실행 봉투로 옮기는 창구를 검증한다."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from tracer_agent.shared.workflows.jobs_envelope import ENVELOPE_UNAVAILABLE, JobEnvelopeClient

BASE_URL = "http://tracer-api:3902"

DATA: dict[str, Any] = {
    "model": "claude-sonnet-4-6",
    "fallbackModel": None,
    "apiKey": "sk-test",
    "modelRates": {"claude-haiku-4-5": {"input": 1, "output": 5, "cacheWrite": 1, "cacheRead": 1}},
    "limits": {"budgetUsd": 1.2, "maxTurns": 14, "maxOutputTokens": 4000},
    "deadlineMs": 720_000,
}


def client(handler: Any) -> JobEnvelopeClient:
    transport = httpx.MockTransport(handler)
    return JobEnvelopeClient(httpx.AsyncClient(transport=transport), BASE_URL)


async def test_받은_값을_실행_봉투로_가른다() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "data": DATA})

    envelope = await client(respond).issue("recipe-scan", "user-1")

    assert calls == [f"{BASE_URL}/internal/jobs/recipe-scan/envelope"]
    assert envelope.model == "claude-sonnet-4-6"
    assert envelope.api_key == "sk-test"
    assert envelope.limits["maxTurns"] == 14
    assert envelope.deadline_ms == 720_000
    assert envelope.model_rates["claude-haiku-4-5"]["input"] == 1


async def test_서버가_거절하면_다시_태우지_않는다() -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "error": {"code": "job.llm-key-missing"}})

    with pytest.raises(ApplicationError) as raised:
        await client(refuse).issue("recipe-scan", "user-1")

    assert raised.value.type == ENVELOPE_UNAVAILABLE
    assert raised.value.non_retryable is True


async def test_서버가_흔들리면_다시_태운다() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with pytest.raises(ApplicationError) as raised:
        await client(fail).issue("recipe-scan", "user-1")

    assert raised.value.non_retryable is False


async def test_데드라인_없는_봉투는_다시_태우지_않는다() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        without_deadline = {key: value for key, value in DATA.items() if key != "deadlineMs"}
        return httpx.Response(200, json={"ok": True, "data": without_deadline})

    with pytest.raises(ApplicationError) as raised:
        await client(respond).issue("recipe-scan", "user-1")

    assert raised.value.non_retryable is True


async def test_봉투가_망가지면_다시_태우지_않는다() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "data": {"apiKey": "sk-test"}})

    with pytest.raises(ApplicationError) as raised:
        await client(respond).issue("recipe-scan", "user-1")

    assert raised.value.non_retryable is True
