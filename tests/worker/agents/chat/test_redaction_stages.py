"""도구가 되읽은 것과 모델이 낸 답이 사용자에게 닿기 전에 가려지는지 검증한다."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from tests.support.chat_api import chat_confirmation_response
from tests.support.contract import shared_contract
from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import CHAT_PROMPT
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.chat.reader import ChatReadClient
from tracer_agent.worker.agents.chat.tools import build_chat_registry
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair

_MARKER = str(shared_contract("redaction.json")["marker"])
_CREDENTIAL = "sk-ant-api03-vJ8xQ2mNbR7tZ1wLpK4hYdF6sA9cE0gU"
_BASE_URL = "http://tracer-api.test"


def _read_tool(body: dict[str, Any]) -> Any:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "data": body})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = ChatReadClient(http, _BASE_URL, "user-1", "scope-token")
    registry = build_chat_registry(client, [], {}, agent_name="chat")
    tool = next(item for item in registry.langchain_tools() if item.name == "get_task")
    return tool, http


async def test_되읽은_것에_담긴_자격은_모델이_받는_문자열에_없다() -> None:
    tool, http = _read_tool({"taskId": "task-1", "note": f"Authorization: Bearer {_CREDENTIAL}"})

    async with http:
        text = await tool.coroutine(taskId="task-1")

    assert _CREDENTIAL not in text
    assert json.loads(text) == {"taskId": "task-1", "note": f"Authorization: {_MARKER}"}


async def test_key가_평범해도_값이_자격이면_가려진다() -> None:
    tool, http = _read_tool({"summary": _CREDENTIAL})

    async with http:
        text = await tool.coroutine(taskId="task-1")

    assert json.loads(text) == {"summary": _MARKER}


async def test_추적을_끈_실행에서도_되읽은_것이_가려진다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(os.environ, "LANGSMITH_TRACING", "false")
    tool, http = _read_tool({"note": _CREDENTIAL})

    async with http:
        text = await tool.coroutine(taskId="task-1")

    assert _CREDENTIAL not in text


async def test_자격을_말하기만_한_결과는_그대로_모델에게_간다() -> None:
    tool, http = _read_tool({"note": "The bearer of this token is the worker."})

    async with http:
        text = await tool.coroutine(taskId="task-1")

    assert json.loads(text) == {"note": "The bearer of this token is the worker."}


def _request() -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "model": "claude-haiku-4-5",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "threadId": "thread-1",
            "executionId": "execution-1",
            "userId": "user-1",
            "language": "ko",
            "messages": [{"role": "user", "content": "토큰 알려줘"}],
            "agentApiBaseUrl": "http://agent-api.test",
        }
    )


async def _answer(monkeypatch: pytest.MonkeyPatch, text: str) -> str:
    monkeypatch.setattr(
        chat_mod, "make_chat_pair", lambda *_a, **_k: ChatPair(FakeToolLoopChat([text]), None)
    )
    transport = httpx.MockTransport(chat_confirmation_response)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await chat_mod.run_chat(_request(), client, ExecutionTrace(), CHAT_PROMPT)
    return str(result["assistantText"])


async def test_모델의_답에_실린_자격은_사용자에게_나가기_전에_가려진다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = await _answer(monkeypatch, f"토큰은 {_CREDENTIAL} 입니다")

    assert _CREDENTIAL not in answer
    assert f"토큰은 {_MARKER} 입니다" in answer


async def test_자격이_없는_답은_그대로_사용자에게_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = await _answer(monkeypatch, "정리했습니다")

    assert "정리했습니다" in answer
