"""chat 쓰기·기억 도구가 도구를 부른 자리에서 tracer-api 창구를 부르는지 검증한다."""

from __future__ import annotations

import json
from functools import partial
from types import SimpleNamespace
from typing import Any

import httpx

from tests.support.chat_api import FakeChatMemoryApi, chat_confirmation_response
from tests.support.fakes import mk_tool_runtime
from tracer_agent.shared.agents.chat.models import ProposedWrite
from tracer_agent.worker.agents.chat.memory import ChatMemoryClient
from tracer_agent.worker.agents.chat.store import ChatMemoryStore
from tracer_agent.worker.agents.chat.tools import build_chat_registry
from tracer_agent.worker.agents.chat.tools.registry import INVALID_ARGS
from tracer_agent.worker.agents.chat.tools.specs import READ_TOOL_NAMES
from tracer_agent.worker.agents.chat.writer import ChatWriteClient

_BASE_URL = "http://tracer-api.test"


def _tool(client: ChatWriteClient | None, proposals: list[ProposedWrite]) -> Any:
    registry = build_chat_registry(None, proposals, {}, agent_name="chat", write_client=client)
    return next(t for t in registry.langchain_tools() if t.name == "propose_task_write")


def _write_tool(name: str, client: ChatWriteClient | None, proposals: list[ProposedWrite]) -> Any:
    registry = build_chat_registry(None, proposals, {}, agent_name="chat", write_client=client)
    return next(t for t in registry.langchain_tools() if t.name == name).coroutine


def _memory_tools(client: ChatMemoryClient | None) -> dict[str, Any]:
    """기억 도구는 저장소를 런타임으로 받으므로 그래프가 주입하는 인자를 여기서 대신 묶는다."""
    registry = build_chat_registry(None, [], {}, agent_name="chat")
    runtime = mk_tool_runtime(None if client is None else ChatMemoryStore(client))
    return {
        tool.name: partial(tool.coroutine, runtime=runtime)
        for tool in registry.langchain_tools()
        if tool.name in {"recall_facts"}
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
        payload = json.loads(await _tool(client, proposals).coroutine(action="archive", taskId="task-1"))

    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"{_BASE_URL}/api/agent/chat/threads/thread-1/confirmations"
    # 사용자 범위는 도구 인자가 아니라 진입점을 만들 때 묶인 자격이 정한다.
    assert seen[0].headers["authorization"] == "Bearer scope-token"
    assert json.loads(seen[0].content) == {
        "toolName": "propose_task_write",
        "args": {"action": "archive", "taskId": "task-1"},
    }
    # 모델이 읽는 확인 대기 문장은 서버가 만들어 그대로 돌아오므로 두 백엔드가 같은 문장을 본다.
    assert payload["confirmationId"] == "conf-propose_task_write"
    assert payload["status"] == "pending"


async def test_세운_대기_행의_id를_산출물이_인용한다() -> None:
    proposals: list[ProposedWrite] = []
    client, http = _client(httpx.MockTransport(chat_confirmation_response))
    async with http:
        await _tool(client, proposals).coroutine(action="archive", taskId="task-1")

    assert proposals == [
        ProposedWrite(
            confirmationId="conf-propose_task_write",
            toolName="propose_task_write",
            args={"action": "archive", "taskId": "task-1"},
        )
    ]


async def test_창구가_거절하면_계약_문구로_알리고_산출물에_남기지_않는다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"ok": False})

    proposals: list[ProposedWrite] = []
    client, http = _client(httpx.MockTransport(handle))
    async with http:
        text = await _tool(client, proposals).coroutine(action="archive", taskId="task-1")

    assert "propose_task_write" in text
    assert "404" in text
    assert proposals == []


async def test_확인_창구가_없는_실행은_제안을_지어내지_않는다() -> None:
    proposals: list[ProposedWrite] = []

    text = await _tool(None, proposals).coroutine(action="archive", taskId="task-1")

    assert "propose_task_write" in text
    assert "{" not in text
    assert proposals == []


async def test_사실을_남기는_도구가_승인_대기로_올라간다() -> None:
    proposals: list[ProposedWrite] = []
    client, http = _client(httpx.MockTransport(chat_confirmation_response))

    async with http:
        payload = json.loads(
            await _write_tool("remember_fact", client, proposals)(key="lang", content="한국어를 쓴다")
        )

    assert payload["confirmationId"] == "conf-remember_fact"
    assert proposals == [
        ProposedWrite(
            confirmationId="conf-remember_fact",
            toolName="remember_fact",
            args={"key": "lang", "content": "한국어를 쓴다"},
        )
    ]


async def test_사실을_남기는_도구가_기억_창구를_직접_부르지_않는다() -> None:
    부른것: list[httpx.Request] = []
    proposals: list[ProposedWrite] = []

    def handle(request: httpx.Request) -> httpx.Response:
        부른것.append(request)
        return chat_confirmation_response(request)

    client, http = _client(httpx.MockTransport(handle))
    async with http:
        await _write_tool("remember_fact", client, proposals)(key="lang", content="한국어를 쓴다")

    부른곳 = [str(request.url) for request in 부른것]

    assert len(부른곳) == 1
    assert 부른곳[0].endswith("/confirmations")
    assert "/memories/" not in 부른곳[0]


async def test_되읽는_도구는_승인을_기다리지_않는다() -> None:
    api = FakeChatMemoryApi()
    api.facts["lang"] = "한국어를 쓴다"
    client, http = _memory_client(httpx.MockTransport(api.handle))

    async with http:
        payload = json.loads(await _memory_tools(client)["recall_facts"]())

    assert payload["facts"] == [
        {"key": "lang", "content": "한국어를 쓴다", "updatedAt": "2026-01-01T00:00:00.000Z"}
    ]


async def test_기억_API가_없는_실행은_되읽었다고_말하지_않는다() -> None:
    recalled = await _memory_tools(None)["recall_facts"]()

    assert "recall_facts" in recalled
    assert "{" not in recalled


class Test도구인자검증:
    async def test_계약_밖의_인자는_도구_실패로_모델에게_알린다(self) -> None:
        registry = build_chat_registry(None, [], {}, agent_name="chat")
        tool = next(one for one in registry.langchain_tools() if one.name in READ_TOOL_NAMES)

        answered = await tool.coroutine(unknown_argument="값")  # type: ignore[misc]

        assert "failed" in answered
        assert INVALID_ARGS in answered

    async def test_검증을_통과한_인자는_와이어_이름으로_조회에_넘어간다(self) -> None:
        seen: dict[str, object] = {}

        class _Client:
            async def read(self, name: str, args: dict[str, object]) -> Any:
                seen.update({"name": name, "args": args})
                return SimpleNamespace(ok=True, status_code=200, text="{}")

        registry = build_chat_registry(_Client(), [], {}, agent_name="chat")  # type: ignore[arg-type]
        tool = next(one for one in registry.langchain_tools() if one.name == "get_task")

        await tool.coroutine(taskId="t1")  # type: ignore[misc]

        assert seen["name"] == "get_task"
        assert seen["args"] == {"taskId": "t1"}
