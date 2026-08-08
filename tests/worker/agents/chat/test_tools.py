"""chat 도구가 실행 기계 위에서 되읽기·확인·기억 창구를 부르는지 검증한다(네트워크 없음)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tests.support.chat_api import FakeChatMemoryApi, chat_confirmation_response
from tests.support.fakes import mk_rates
from tracer_agent.shared.agents.chat.models import ProposedWrite
from tracer_agent.shared.agents.chat.tools.surface import chat_argument_rejection
from tracer_agent.worker.agents.chat.backends import (
    MEMORY_BACKEND_MISSING,
    READ_BACKEND_MISSING,
    WRITE_BACKEND_MISSING,
    ChatMemoryFacts,
    ChatReadPort,
    ChatTurnBackends,
    ChatWritePort,
    UnwiredChatMemory,
    UnwiredReadClient,
    UnwiredWriteClient,
)
from tracer_agent.worker.agents.chat.memory import ChatMemoryClient
from tracer_agent.worker.agents.chat.reader import ChatReadClient
from tracer_agent.worker.agents.chat.store import ChatMemoryStore
from tracer_agent.worker.agents.chat.tools import ChatToolContext, chat_tool_registry
from tracer_agent.worker.agents.chat.tools.registry import (
    INVALID_ARGS,
    chat_tool_arguments_missing,
    chat_tool_failed,
)
from tracer_agent.worker.agents.chat.writer import ChatWriteClient
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import single_loop_budget

_BASE_URL = "http://tracer-api.test"


def _backends(
    *,
    read: ChatReadPort | None = None,
    write: ChatWritePort | None = None,
    memory: ChatMemoryClient | None = None,
) -> ChatTurnBackends:
    return ChatTurnBackends(
        read=read or UnwiredReadClient(),
        agent_read=read or UnwiredReadClient(),
        write=write or UnwiredWriteClient(),
        memory=UnwiredChatMemory() if memory is None else ChatMemoryFacts(ChatMemoryStore(memory)),
    )


def _context(backends: ChatTurnBackends, proposals: list[ProposedWrite] | None = None) -> ChatToolContext:
    return ChatToolContext(
        agent_name="chat",
        trace=ExecutionTrace(),
        budget=single_loop_budget("chat", "claude-haiku-4-5", 2.0, mk_rates(), 0.0),
        max_model_turns=8,
        tool_owner="chat",
        backends=backends,
        proposals=proposals if proposals is not None else [],
    )


async def _call(name: str, args: dict[str, Any], context: ChatToolContext) -> str:
    return await chat_tool_registry({}).invoke(name, args, context)


def _write_client(transport: httpx.MockTransport) -> tuple[ChatWriteClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    return ChatWriteClient(http, _BASE_URL, "user-1", "thread-1", "scope-token"), http


def _memory_client(transport: httpx.MockTransport) -> tuple[ChatMemoryClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    return ChatMemoryClient(http, _BASE_URL, "user-1", "scope-token"), http


async def test_쓰기_도구가_실행_대신_확인_창구에_대기_행을_세운다() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return chat_confirmation_response(request)

    client, http = _write_client(httpx.MockTransport(handle))
    async with http:
        answered = await _call(
            "propose_task_write",
            {"action": "archive", "taskId": "task-1"},
            _context(_backends(write=client)),
        )

    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"{_BASE_URL}/api/agent/chat/threads/thread-1/confirmations"
    # 사용자 범위는 도구 인자가 아니라 진입점을 만들 때 묶인 자격이 정한다.
    assert seen[0].headers["authorization"] == "Bearer scope-token"
    assert json.loads(seen[0].content) == {
        "toolName": "propose_task_write",
        "args": {"action": "archive", "taskId": "task-1"},
    }
    # 모델이 읽는 확인 대기 문장은 서버가 만들어 그대로 돌아오므로 두 백엔드가 같은 문장을 본다.
    payload = json.loads(answered)
    assert payload["confirmationId"] == "conf-propose_task_write"
    assert payload["status"] == "pending"


async def test_세운_대기_행의_id를_산출물이_인용한다() -> None:
    proposals: list[ProposedWrite] = []
    client, http = _write_client(httpx.MockTransport(chat_confirmation_response))
    async with http:
        await _call(
            "propose_task_write",
            {"action": "archive", "taskId": "task-1"},
            _context(_backends(write=client), proposals),
        )

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
    client, http = _write_client(httpx.MockTransport(handle))
    async with http:
        answered = await _call(
            "propose_task_write",
            {"action": "archive", "taskId": "task-1"},
            _context(_backends(write=client), proposals),
        )

    assert answered == chat_tool_failed("propose_task_write", "the confirmation API answered 404")
    assert proposals == []


async def test_인자가_빠진_거절에는_포기_지시_대신_고쳐_부르라는_문구가_간다() -> None:
    rejection = chat_argument_rejection()

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            int(rejection["status"]),
            json={
                "ok": False,
                "error": {
                    "code": rejection["code"],
                    "message": rejection["message"],
                    "details": {"action": "archive", "missing": ["taskId"]},
                },
            },
        )

    proposals: list[ProposedWrite] = []
    client, http = _write_client(httpx.MockTransport(handle))
    async with http:
        answered = await _call(
            "propose_task_write",
            {"action": "archive", "taskId": "task-1"},
            _context(_backends(write=client), proposals),
        )

    assert answered == chat_tool_arguments_missing("propose_task_write", "archive", ["taskId"])
    assert proposals == []


async def test_확인_창구가_없는_실행은_제안을_지어내지_않는다() -> None:
    proposals: list[ProposedWrite] = []

    answered = await _call(
        "propose_task_write",
        {"action": "archive", "taskId": "task-1"},
        _context(_backends(), proposals),
    )

    # 배선되지 않은 창구가 모델에게 대는 사유는 실행 기계로 옮긴 뒤에도 같은 문장이다.
    assert answered == chat_tool_failed("propose_task_write", WRITE_BACKEND_MISSING)
    assert proposals == []


async def test_사실을_남기는_도구가_승인_대기로_올라간다() -> None:
    proposals: list[ProposedWrite] = []
    client, http = _write_client(httpx.MockTransport(chat_confirmation_response))

    async with http:
        answered = await _call(
            "remember_fact",
            {"key": "lang", "content": "한국어를 쓴다"},
            _context(_backends(write=client), proposals),
        )

    assert json.loads(answered)["confirmationId"] == "conf-remember_fact"
    assert proposals == [
        ProposedWrite(
            confirmationId="conf-remember_fact",
            toolName="remember_fact",
            args={"key": "lang", "content": "한국어를 쓴다"},
        )
    ]


async def test_사실을_남기는_도구가_기억_창구를_직접_부르지_않는다() -> None:
    부른것: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        부른것.append(request)
        return chat_confirmation_response(request)

    client, http = _write_client(httpx.MockTransport(handle))
    async with http:
        await _call(
            "remember_fact",
            {"key": "lang", "content": "한국어를 쓴다"},
            _context(_backends(write=client)),
        )

    부른곳 = [str(request.url) for request in 부른것]

    assert len(부른곳) == 1
    assert 부른곳[0].endswith("/confirmations")
    assert "/memories/" not in 부른곳[0]


async def test_되읽는_도구는_승인을_기다리지_않는다() -> None:
    api = FakeChatMemoryApi()
    api.facts["lang"] = "한국어를 쓴다"
    client, http = _memory_client(httpx.MockTransport(api.handle))

    async with http:
        answered = await _call("recall_facts", {}, _context(_backends(memory=client)))

    assert json.loads(answered)["facts"] == [
        {"key": "lang", "content": "한국어를 쓴다", "updatedAt": "2026-01-01T00:00:00.000Z"}
    ]


async def test_기억_API가_없는_실행은_되읽었다고_말하지_않는다() -> None:
    answered = await _call("recall_facts", {}, _context(_backends()))

    assert answered == chat_tool_failed("recall_facts", MEMORY_BACKEND_MISSING)


async def test_기억_API가_거절하면_그_상태를_사유로_모델에게_알린다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"ok": False})

    client, http = _memory_client(httpx.MockTransport(handle))
    async with http:
        answered = await _call("recall_facts", {}, _context(_backends(memory=client)))

    assert answered == chat_tool_failed("recall_facts", "the memory API answered 503")


def test_같은_설명을_받은_턴은_도구를_다시_세우지_않는다() -> None:
    descriptions = {"get_task": "태스크 하나를 읽는다"}

    assert chat_tool_registry(descriptions) is chat_tool_registry(dict(descriptions))
    assert chat_tool_registry(descriptions) is not chat_tool_registry({})


class Test도구인자검증:
    async def test_계약_밖의_인자는_도구_실패로_모델에게_알린다(self) -> None:
        answered = await _call("get_task", {"unknown_argument": "값"}, _context(_backends()))

        assert answered == chat_tool_failed("get_task", INVALID_ARGS)

    async def test_검증을_통과한_인자는_와이어_이름으로_조회에_넘어간다(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True, "data": {"task": {"id": "t1"}}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        read = ChatReadClient(http, _BASE_URL, "user-1", "scope-token")
        async with http:
            answered = await _call("get_task", {"taskId": "t1"}, _context(_backends(read=read)))

        assert str(seen[0].url) == f"{_BASE_URL}/api/v1/tasks/t1"
        # 성공 봉투는 벗겨져 두 구현체의 모델이 같은 필드를 본다.
        assert json.loads(answered) == {"task": {"id": "t1"}}

    async def test_되읽기_진입점이_없는_실행은_되읽었다고_말하지_않는다(self) -> None:
        answered = await _call("get_task", {"taskId": "t1"}, _context(_backends()))

        assert answered == chat_tool_failed("get_task", READ_BACKEND_MISSING)
