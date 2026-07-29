"""chat 도구 루프가 답변과 확인 대기 행과 기억 적재를 내는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.support.chat_api import FakeChatMemoryApi
from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.narrate import narrate
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.shared.agents.shared.models import AgentResponse
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.runtime.execution.runner import execute

_READ_API = "http://tracer-api.test"


def _request(**overrides: Any) -> ChatRequest:
    values: dict[str, Any] = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "jobId": "job-1",
        "threadId": "thread-1",
        "executionId": "execution-1",
        "userId": "user-1",
        "language": "ko",
        "messages": [{"role": "user", "content": "task-1 아카이브해줘"}],
    }
    values.update(overrides)
    return ChatRequest.model_validate(values)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    turns: list[Any],
    memory: FakeChatMemoryApi | None = None,
    **overrides: Any,
) -> AgentResponse:
    chat = FakeToolLoopChat(turns)
    monkeypatch.setattr(chat_mod, "make_chat", lambda *_args, **_kwargs: chat)
    req = _request(readApiBaseUrl=_READ_API, **overrides)
    transport = httpx.MockTransport((memory or FakeChatMemoryApi()).handle)
    async with httpx.AsyncClient(transport=transport) as client:
        return await execute(
            "chat",
            req.model,
            req.deadlineMs,
            lambda usage: chat_mod.run_chat(req, client, usage),
        )


async def test_쓰기_도구는_실행_대신_제안으로_기록되고_답변이_나온다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run(
        monkeypatch,
        [
            [{"name": "archive_task", "args": {"taskId": "task-1"}}],
            "task-1 아카이브를 제안했고 승인을 기다립니다.",
        ],
    )

    narrate("chat :: 쓰기 도구를 제안으로 기록하고 답변을 낸다", result)
    data = result.data or {}
    proposals = data["proposedWrites"]
    # 확인 대기 행은 도구를 부른 그 자리에서 창구가 세우고, 산출물은 그 id를 인용만 한다.
    assert proposals == [
        {
            "confirmationId": "conf-archive_task",
            "toolName": "archive_task",
            "args": {"taskId": "task-1"},
        }
    ]
    assert isinstance(data["assistantText"], str)
    assert data["assistantText"] != ""


async def test_remember_fact는_턴이_끝나기_전에_기억_API에_적재된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = FakeChatMemoryApi()
    result = await _run(
        monkeypatch,
        [
            [{"name": "remember_fact", "args": {"key": "lang", "content": "한국어를 쓴다"}}],
            "기억했습니다.",
        ],
        memory=memory,
    )

    narrate("chat :: remember_fact를 부른 자리에서 기억 API에 적재한다", result)
    data = result.data or {}
    # 산출물에 기억이 실리지 않으므로 적재됐다는 증거는 기억 API에만 있다.
    assert memory.facts == {"lang": "한국어를 쓴다"}
    assert data["proposedWrites"] == []


def _replay_handler(replay: dict[str, Any] | None, memory: FakeChatMemoryApi) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/replay"):
            if replay is None:
                return httpx.Response(404, json={"ok": False})
            return httpx.Response(200, json={"ok": True, "data": replay})
        return memory.handle(request)

    return handle


async def _run_replay(
    monkeypatch: pytest.MonkeyPatch,
    replay: dict[str, Any] | None,
    turns: list[Any],
    memory: FakeChatMemoryApi | None = None,
    **overrides: Any,
) -> tuple[AgentResponse, FakeToolLoopChat]:
    chat = FakeToolLoopChat(turns)
    monkeypatch.setattr(chat_mod, "make_chat", lambda *_args, **_kwargs: chat)
    # 봉투에 이력이 없어야 그래프가 서버 재생 API를 문맥의 출처로 삼는다.
    req = _request(readApiBaseUrl=_READ_API, messages=[], **overrides)
    transport = httpx.MockTransport(_replay_handler(replay, memory or FakeChatMemoryApi()))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await execute(
            "chat", req.model, req.deadlineMs, lambda usage: chat_mod.run_chat(req, client, usage)
        )
    return response, chat


async def test_서버가_재생해_준_지난_이력_위에서_턴을_잇는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, chat = await _run_replay(
        monkeypatch,
        {
            "messages": [
                {"role": "user", "content": "task-1 만들어줘"},
                {"role": "assistant", "content": "task-1을 만들었습니다."},
                {"role": "user", "content": "그거 뭐였지?"},
            ],
            "summary": None,
            "facts": [],
        },
        ["예전에 만든 task-1 이야기입니다."],
    )

    narrate("chat :: 서버가 재생해 준 지난 이력 위에서 이번 턴을 잇는다", result)
    seen = " ".join(
        str(getattr(message, "content", message)) for request in chat.requests for message in request
    )
    # 창 자르기도 짝 맞추기도 서버가 끝낸 이력이 이번 턴 모델 입력에 그대로 보인다.
    assert "task-1 만들어줘" in seen
    assert "task-1을 만들었습니다." in seen
    assert "그거 뭐였지?" in seen


async def test_recall_facts는_기억_API가_든_사실을_되읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = FakeChatMemoryApi()
    memory.facts["lang"] = "한국어를 쓴다"
    result, chat = await _run_replay(
        monkeypatch,
        {
            "messages": [{"role": "user", "content": "내가 뭘 쓴다고 했지?"}],
            "summary": None,
            "facts": [],
        },
        [
            [{"name": "recall_facts", "args": {}}],
            "기억하고 있는 사실을 확인했습니다.",
        ],
        memory=memory,
    )

    narrate("chat :: recall_facts가 기억 API를 되읽는다", result)
    seen = " ".join(
        str(getattr(message, "content", message)) for request in chat.requests for message in request
    )
    # 턴 시작 스냅샷이 비어 있어도 도구가 API를 읽어 모델에게 되돌린다.
    assert "한국어를 쓴다" in seen
    assert (result.data or {})["assistantText"] != ""


async def test_이력을_못_읽으면_빈_이력으로_모델을_부르지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, chat = await _run_replay(monkeypatch, None, ["아무 말이나 합니다."])

    narrate("chat :: 이력을 못 읽으면 모델을 부르지 않고 실행이 실패한다", result)
    assert chat.requests == []
    assert result.data is None
    assert result.error is not None
