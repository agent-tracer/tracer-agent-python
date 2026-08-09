"""단가와 한도와 자격을 서버에서 받아 실행 봉투로 옮기는 창구를 검증한다."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from tracer_agent.worker.workflows.envelope import ENVELOPE_UNAVAILABLE, ChatEnvelopeClient

BASE_URL = "http://tracer-api:3902"

DATA: dict[str, Any] = {
    "model": "claude-haiku-4-5",
    "apiKey": "sk-test",
    "modelRates": {"claude-haiku-4-5": {"input": 1, "output": 5, "cacheWrite": 1, "cacheRead": 1}},
    "limits": {"budgetUsd": 1.2, "maxTurns": 14, "maxOutputTokens": 4000},
    "deadlineMs": 600000,
    "readApiBaseUrl": BASE_URL,
    "scopeToken": "ms1.x.y",
    "toolDescriptions": {"get_timeline": "설명"},
}


def client(handler: Any) -> ChatEnvelopeClient:
    transport = httpx.MockTransport(handler)
    return ChatEnvelopeClient(httpx.AsyncClient(transport=transport), BASE_URL)


async def test_받은_값을_봉투_조각으로_옮긴다() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "data": DATA})

    envelope = await client(respond).issue("e1", 2)

    assert calls == [f"{BASE_URL}/internal/chat/executions/e1/envelope"]
    assert envelope.fields["apiKey"] == "sk-test"
    assert envelope.fields["limits"]["maxTurns"] == 14
    # 궤적이 이번 시도를 잇도록 시도 번호를 봉투가 싣는다.
    assert envelope.fields["attempt"] == 2


async def test_서버가_거절하면_다시_태우지_않는다() -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "error": {"code": "chat.llm-key-missing"}})

    with pytest.raises(ApplicationError) as raised:
        await client(refuse).issue("e1", 1)

    assert raised.value.type == ENVELOPE_UNAVAILABLE
    assert raised.value.non_retryable is True


async def test_서버가_흔들리면_다시_태운다() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with pytest.raises(ApplicationError) as raised:
        await client(fail).issue("e1", 1)

    assert raised.value.non_retryable is False
