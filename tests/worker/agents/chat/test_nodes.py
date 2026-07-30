"""chat 대화 노드를 그래프 밖에서 실행해 draft 배달과 최종 result 계약을 검증한다."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from tests.support.chat_api import chat_confirmation_response
from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tracer_agent.shared.agents.chat.models import ChatRequest, DraftCallback
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.chat.drafts import ChatExecutionClosed, DraftPublisher
from tracer_agent.worker.agents.chat.nodes.converse import _final_text
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace


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
        "agentApiBaseUrl": "http://agent-api.test",
        "draftCallback": {
            "url": "http://agent-api.test/api/v1/chat/executions/execution-1/drafts",
            "token": "grant",
            "attempt": 2,
        },
    }
    values.update(overrides)
    return ChatRequest.model_validate(values)


async def _run(
    monkeypatch: pytest.MonkeyPatch, turns: list[Any], **overrides: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    chat = FakeToolLoopChat(turns)
    monkeypatch.setattr(chat_mod, "make_chat", lambda *_args, **_kwargs: chat)
    posted: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/confirmations"):
            return chat_confirmation_response(request)
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"stored": True})

    req = _request(**overrides)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await chat_mod.run_chat(req, client, ExecutionTrace())
    return result, posted


async def test_접수된_실행은_누적_답변을_창구로_되돌려_보낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, posted = await _run(monkeypatch, ["정리했습니다"])

    assert posted, "누적 답변이 최소 한 번은 창구로 나가야 한다"
    assert [body["draftSeq"] for body in posted] == sorted(body["draftSeq"] for body in posted)
    assert all(body["token"] == "grant" and body["attempt"] == 2 for body in posted)
    # 창구는 조각이 아니라 그 시점까지의 전문을 받는다.
    assert posted[-1]["text"] == result["assistantText"]
    assert result["assistantText"] != ""


async def test_서버가_종결을_알리면_실행을_더_끌지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 취소 레지스트리는 프로세스 로컬이라 다른 인스턴스에서 돈 실행에는 닿지 않는다.
    chat = FakeToolLoopChat(["한참 답하는 중"])
    monkeypatch.setattr(chat_mod, "make_chat", lambda *_args, **_kwargs: chat)

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stored": False, "terminal": True})

    req = _request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ChatExecutionClosed):
            await chat_mod.run_chat(req, client, ExecutionTrace())


async def test_창구가_없으면_draft를_보내지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    result, posted = await _run(monkeypatch, ["정리했습니다"], draftCallback=None)

    assert posted == []
    assert result["assistantText"] != ""


async def test_접수된_실행의_결과가_제안_쓰기를_실행_없이_담아_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _posted = await _run(
        monkeypatch,
        [
            [{"name": "archive_task", "args": {"taskId": "task-1"}}],
            "task-1 아카이브를 제안했고 승인을 기다립니다.",
        ],
        readApiBaseUrl="http://tracer-api.test",
    )

    assert result["proposedWrites"] == [
        {
            "confirmationId": "conf-archive_task",
            "toolName": "archive_task",
            "args": {"taskId": "task-1"},
        }
    ]
    assert result["assistantText"] != ""


def test_최종_답변은_도구_호출이_없는_마지막_어시스턴트_텍스트다() -> None:
    messages = [
        AIMessage(
            content="확인해볼게요",
            tool_calls=[{"id": "call-1", "name": "search_tasks", "args": {}, "type": "tool_call"}],
        ),
        ToolMessage(content="검색 결과", tool_call_id="call-1"),
        AIMessage(content="최종 답변"),
    ]

    assert _final_text(messages) == "최종 답변"


async def test_창구_전송은_간격이_지난_뒤에만_묶어_보낸다() -> None:
    posted: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"stored": True})

    ticks = iter([0.0, 0.16, 0.16, 0.20, 0.30])
    callback = DraftCallback.model_validate(
        {"url": "http://tracer-api.test/drafts", "token": "grant", "attempt": 1}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        publisher = DraftPublisher(client, callback, lambda: next(ticks))
        await publisher.push("정")
        await publisher.push("리")
        await publisher.push("했다")
        await publisher.flush()

    # 간격 안에 들어온 조각은 묶이고, 창구는 매번 그때까지의 전문을 받는다.
    assert [body["text"] for body in posted] == ["정리", "정리했다"]
    assert [body["draftSeq"] for body in posted] == [1, 2]


async def _system_content(
    monkeypatch: pytest.MonkeyPatch, turns: list[Any], **overrides: Any
) -> tuple[FakeToolLoopChat, list[Any]]:
    chat = FakeToolLoopChat(turns)
    monkeypatch.setattr(chat_mod, "make_chat", lambda *_args, **_kwargs: chat)
    req = _request(draftCallback=None, **overrides)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        await chat_mod.run_chat(req, client, ExecutionTrace())
    return chat, list(chat.requests[0][0].content)


async def test_사실과_요약은_시스템_캐시_경계_뒤에_붙는다(monkeypatch: pytest.MonkeyPatch) -> None:
    _chat, content = await _system_content(
        monkeypatch,
        ["정리했습니다"],
        summary="앞선 대화 요약",
        facts=[{"key": "editor", "content": "vim을 쓴다"}],
    )

    assert [("cache_control" in block) for block in content] == [True, False]
    assert "vim을 쓴다" not in content[0]["text"]
    assert "vim을 쓴다" in content[1]["text"] and "앞선 대화 요약" in content[1]["text"]


async def test_사실이_늘어도_캐시되는_시스템_접두사는_그대로다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 접두사 일치라 사실 하나가 경계 앞에 섞이면 remember_fact 한 번에 시스템 캐시가 통째로 죽는다.
    _first, before = await _system_content(monkeypatch, ["정리했습니다"], summary="요약 A", facts=[])
    chat, after = await _system_content(
        monkeypatch,
        [[{"name": "search_tasks", "args": {"query": "x"}}], "정리했습니다"],
        summary="요약 B",
        facts=[{"key": "editor", "content": "vim을 쓴다"}],
    )

    assert before[0] == after[0]
    assert before[1]["text"] != after[1]["text"]
    # 시스템 하나에 최근 도구 결과 둘을 더해도 공급자 상한인 네 경계를 넘지 않는다.
    assert 1 < chat.cached_blocks() <= 4
