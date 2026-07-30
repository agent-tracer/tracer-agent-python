"""chat 쓰기·기억 도구가 도구를 부른 자리에서 tracer-api 창구를 부르는지 검증한다."""

from __future__ import annotations

import json
from functools import partial
from typing import Any

import httpx
import pytest

from tests.support.chat_api import FakeChatMemoryApi, chat_confirmation_response
from tests.support.fakes import mk_tool_runtime
from tracer_agent.shared.agents.chat.models import ProposedWrite
from tracer_agent.worker.agents.chat.memory import ChatMemoryClient
from tracer_agent.worker.agents.chat.store import ChatMemoryStore
from tracer_agent.worker.agents.chat.tools import build_chat_registry
from tracer_agent.worker.agents.chat.writer import ChatWriteClient

_BASE_URL = "http://tracer-api.test"


def _tool(client: ChatWriteClient | None, proposals: list[ProposedWrite]) -> Any:
    registry = build_chat_registry(None, proposals, {}, agent_name="chat", write_client=client)
    return next(t for t in registry.langchain_tools() if t.name == "archive_task")


def _memory_tools(client: ChatMemoryClient | None) -> dict[str, Any]:
    """기억 도구는 저장소를 런타임으로 받으므로 그래프가 주입하는 인자를 여기서 대신 묶는다."""
    registry = build_chat_registry(None, [], {}, agent_name="chat")
    runtime = mk_tool_runtime(None if client is None else ChatMemoryStore(client))
    return {
        tool.name: partial(tool.coroutine, runtime=runtime)
        for tool in registry.langchain_tools()
        if tool.name in {"recall_facts", "remember_fact"}
    }


def _client(transport: httpx.MockTransport) -> tuple[ChatWriteClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    return ChatWriteClient(http, _BASE_URL, "user-1", "thread-1", "scope-token"), http


def _memory_client(
    transport: httpx.MockTransport,
) -> tuple[ChatMemoryClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    return ChatMemoryClient(http, _BASE_URL, "user-1", "scope-token"), http


async def test_쓰기_도구가_실행_대신_확인_창구에_대기_행을_세운다() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return chat_confirmation_response(request)

    proposals: list[ProposedWrite] = []
    client, http = _client(httpx.MockTransport(handle))
    async with http:
        payload = json.loads(await _tool(client, proposals).coroutine(taskId="task-1"))

    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"{_BASE_URL}/api/agent/chat/threads/thread-1/confirmations"
    # 사용자 범위는 도구 인자가 아니라 진입점을 만들 때 묶인 자격이 정한다.
    assert seen[0].headers["authorization"] == "Bearer scope-token"
    assert json.loads(seen[0].content) == {"toolName": "archive_task", "args": {"taskId": "task-1"}}
    # 모델이 읽는 확인 대기 문장은 서버가 만들어 그대로 돌아오므로 두 백엔드가 같은 문장을 본다.
    assert payload["confirmationId"] == "conf-archive_task"
    assert payload["status"] == "pending"


async def test_세운_대기_행의_id를_산출물이_인용한다() -> None:
    proposals: list[ProposedWrite] = []
    client, http = _client(httpx.MockTransport(chat_confirmation_response))
    async with http:
        await _tool(client, proposals).coroutine(taskId="task-1")

    assert proposals == [
        ProposedWrite(
            confirmationId="conf-archive_task",
            toolName="archive_task",
            args={"taskId": "task-1"},
        )
    ]


async def test_창구가_거절하면_계약_문구로_알리고_산출물에_남기지_않는다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"ok": False})

    proposals: list[ProposedWrite] = []
    client, http = _client(httpx.MockTransport(handle))
    async with http:
        text = await _tool(client, proposals).coroutine(taskId="task-1")

    assert "archive_task" in text
    assert "404" in text
    assert proposals == []


async def test_확인_창구가_없는_실행은_제안을_지어내지_않는다() -> None:
    proposals: list[ProposedWrite] = []

    text = await _tool(None, proposals).coroutine(taskId="task-1")

    assert "archive_task" in text
    assert "{" not in text
    assert proposals == []


async def test_기억_도구가_부른_자리에서_기억_API에_즉시_적재한다() -> None:
    api = FakeChatMemoryApi()
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return api.handle(request)

    client, http = _memory_client(httpx.MockTransport(handle))
    async with http:
        payload = json.loads(
            await _memory_tools(client)["remember_fact"](key="lang", content="한국어를 쓴다")
        )

    assert seen[0].method == "PUT"
    assert str(seen[0].url) == f"{_BASE_URL}/api/agent/chat/memories/lang"
    # 사용자 범위는 도구 인자가 아니라 진입점을 만들 때 묶인 자격이 정한다.
    assert seen[0].headers["authorization"] == "Bearer scope-token"
    assert json.loads(seen[0].content) == {"content": "한국어를 쓴다"}
    assert api.facts == {"lang": "한국어를 쓴다"}
    assert payload == {"key": "lang", "content": "한국어를 쓴다", "status": "remembered"}


async def test_턴이_중간에_끊겨도_그때까지_기억한_것이_남는다() -> None:
    api = FakeChatMemoryApi()
    client, http = _memory_client(httpx.MockTransport(api.handle))
    tools = _memory_tools(client)

    async with http:
        await tools["remember_fact"](key="lang", content="한국어를 쓴다")
        with pytest.raises(RuntimeError):
            await _turn_interrupted(tools)

    # 산출물이 없어도 적재는 이미 끝나 있어야 사용자가 받은 "기억했다"가 사실이 된다.
    assert api.facts == {"lang": "한국어를 쓴다", "tone": "존댓말을 쓴다"}


async def _turn_interrupted(tools: dict[str, Any]) -> None:
    await tools["remember_fact"](key="tone", content="존댓말을 쓴다")
    raise RuntimeError("turn canceled before it produced a result")


async def test_같은_턴의_recall이_방금_적재한_사실을_기억_API에서_읽는다() -> None:
    api = FakeChatMemoryApi()
    client, http = _memory_client(httpx.MockTransport(api.handle))
    tools = _memory_tools(client)

    async with http:
        await tools["remember_fact"](key="lang", content="한국어를 쓴다")
        payload = json.loads(await tools["recall_facts"]())

    assert payload["facts"] == [
        {"key": "lang", "content": "한국어를 쓴다", "updatedAt": "2026-01-01T00:00:00.000Z"}
    ]


async def test_기억_API가_거절하면_계약_문구로_알린다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False})

    client, http = _memory_client(httpx.MockTransport(handle))
    async with http:
        text = await _memory_tools(client)["remember_fact"](key="lang", content="한국어")

    assert "remember_fact" in text
    assert "500" in text


async def test_기억_API가_없는_실행은_기억했다고_말하지_않는다() -> None:
    tools = _memory_tools(None)

    remembered = await tools["remember_fact"](key="lang", content="한국어")
    recalled = await tools["recall_facts"]()

    assert "remember_fact" in remembered
    assert "{" not in remembered
    assert "recall_facts" in recalled
    assert "{" not in recalled
