"""대화 한 턴의 단계가 궤적에 남기는 진입·완료·실패를 고정한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import CHAT_PROMPT
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.shared.agents.shared.models import AgentStepDTO
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair

_AGENT_API = "http://agent-api.test"


def _request(**overrides: Any) -> ChatRequest:
    values: dict[str, Any] = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "threadId": "thread-1",
        "executionId": "execution-1",
        "userId": "user-1",
        "language": "ko",
        "messages": [{"role": "user", "content": "task-1 아카이브해줘"}],
    }
    values.update(overrides)
    return ChatRequest.model_validate(values)


def _orchestration(trace: ExecutionTrace) -> list[AgentStepDTO]:
    return [step for step in trace.steps if step.role == "orchestration"]


def _marks(trace: ExecutionTrace) -> list[tuple[str | None, str | None]]:
    return [(step.eventKind, step.nodeName) for step in _orchestration(trace)]


async def test_턴의_세_단계가_같은_이름으로_궤적에_진입과_완료를_남긴다() -> None:
    trace = ExecutionTrace()
    chats = ChatPair(FakeToolLoopChat(["정리했습니다"]), None)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        await chat_mod.run_chat(_request(), client, trace, CHAT_PROMPT, None, chats)

    assert _marks(trace) == [
        ("node.started", "load_context"),
        ("node.completed", "load_context"),
        ("node.started", "converse"),
        ("node.completed", "converse"),
        ("node.started", "settle"),
        ("node.completed", "settle"),
    ]


async def test_궤적의_단계_문장과_소요_시간이_같은_모양으로_남는다() -> None:
    trace = ExecutionTrace()
    chats = ChatPair(FakeToolLoopChat(["정리했습니다"]), None)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        await chat_mod.run_chat(_request(), client, trace, CHAT_PROMPT, None, chats)

    steps = _orchestration(trace)
    assert [step.content for step in steps] == [
        "chat entered load_context",
        "chat completed load_context",
        "chat entered converse",
        "chat completed converse",
        "chat entered settle",
        "chat completed settle",
    ]
    # 진입은 아직 잰 것이 없고 완료만 그 단계가 쓴 시간을 싣는다.
    assert [step.durationMs is None for step in steps] == [True, False, True, False, True, False]


async def test_문맥_적재는_연결이_끊긴_시도를_다시_걸어_턴을_이어간다() -> None:
    trace = ExecutionTrace()
    chats = ChatPair(FakeToolLoopChat(["정리했습니다"]), None)
    req = _request(agentApiBaseUrl=_AGENT_API, messages=[])
    attempts = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if not request.url.path.endswith("/replay"):
            return httpx.Response(200)
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("replay is unreachable")
        return httpx.Response(200, json={"ok": True, "data": {"messages": [], "summary": None, "facts": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await chat_mod.run_chat(req, client, trace, CHAT_PROMPT, None, chats)

    assert attempts == 2
    # 다시 건 시도도 자기 진입과 실패를 남기므로 궤적이 몇 번을 걸었는지 보인다.
    assert _marks(trace)[:4] == [
        ("node.started", "load_context"),
        ("node.failed", "load_context"),
        ("node.started", "load_context"),
        ("node.completed", "load_context"),
    ]


async def test_단계가_실패하면_그_자리를_궤적에_남기고_실행이_끊긴다() -> None:
    trace = ExecutionTrace()
    chats = ChatPair(FakeToolLoopChat(["정리했습니다"]), None)
    # 봉투에 이력이 없으면 문맥 적재가 재생 창구를 부르고, 그 창구가 거절하면 단계가 실패한다.
    req = _request(agentApiBaseUrl=_AGENT_API, messages=[])

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"ok": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(Exception):  # noqa: B017
            await chat_mod.run_chat(req, client, trace, CHAT_PROMPT, None, chats)

    assert _marks(trace) == [("node.started", "load_context"), ("node.failed", "load_context")]
    assert _orchestration(trace)[-1].content == "chat failed in load_context"
