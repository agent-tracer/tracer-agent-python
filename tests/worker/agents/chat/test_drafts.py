"""대화 단계가 진행 중인 답변을 창구로 되돌려 보내는 리듬을 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

import asyncio
from itertools import count
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage

from tests.support.chat_api import chat_confirmation_response
from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import CHAT_PROMPT
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.shared.agents.chat.surface.contract import chat_draft_rules
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.chat.drafts import ChatExecutionClosed, DraftAck, DraftPublisher
from tracer_agent.worker.agents.chat.steps.converse import _fresh_tool_names
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair


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
        "attempt": 2,
    }
    values.update(overrides)
    return ChatRequest.model_validate(values)


class _Recorder:
    """전송이 아니라 원장에 적힌 것을 세는 대역이며 종결은 원장이 정한다."""

    def __init__(self, terminal: bool = False, gate: asyncio.Event | None = None) -> None:
        self.rows: list[dict[str, Any]] = []
        self._terminal = terminal
        self._gate = gate

    async def __call__(self, seq: int, phase: str, text: str) -> DraftAck:
        self.rows.append({"draftSeq": seq, "phase": phase, "text": text})
        # 실물은 원장 왕복에서 양보하므로 대역도 양보해야 순서가 같아진다.
        await asyncio.sleep(0)
        if self._gate is not None:
            await self._gate.wait()
        return DraftAck(stored=not self._terminal, terminal=self._terminal)


async def _run(turns: list[Any], **overrides: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    chat = FakeToolLoopChat(turns)
    chats = ChatPair(chat, None)
    recorder = _Recorder()

    req = _request(**overrides)
    async with httpx.AsyncClient(transport=httpx.MockTransport(chat_confirmation_response)) as client:
        result = await chat_mod.run_chat(
            req, client, ExecutionTrace(), CHAT_PROMPT, None, chats, DraftPublisher(recorder)
        )
    return result, recorder.rows


async def test_접수된_실행은_누적_답변을_창구로_되돌려_보낸다() -> None:
    result, posted = await _run(["정리했습니다"])

    assert posted, "누적 답변이 최소 한 번은 창구로 나가야 한다"
    assert [body["draftSeq"] for body in posted] == sorted(body["draftSeq"] for body in posted)
    # 원장은 조각이 아니라 그 시점까지의 전문을 받는다.
    assert posted[-1]["text"] == result["assistantText"]
    assert result["assistantText"] != ""


async def test_서버가_종결을_알리면_실행을_더_끌지_않는다() -> None:
    # 취소 레지스트리는 프로세스 로컬이라 다른 인스턴스에서 실행된 것에는 닿지 않는다.
    chat = FakeToolLoopChat(["한참 답하는 중"])
    chats = ChatPair(chat, None)

    req = _request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(chat_confirmation_response)) as client:
        with pytest.raises(ChatExecutionClosed):
            await chat_mod.run_chat(
                req,
                client,
                ExecutionTrace(),
                CHAT_PROMPT,
                None,
                chats,
                DraftPublisher(_Recorder(terminal=True)),
            )


async def test_봉투를_쓰지_않은_응답은_종결로_읽지_않는다() -> None:
    chat = FakeToolLoopChat(["한참 답하는 중"])
    chats = ChatPair(chat, None)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/confirmations"):
            return chat_confirmation_response(request)
        return httpx.Response(200, json={"terminal": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await chat_mod.run_chat(_request(), client, ExecutionTrace(), CHAT_PROMPT, None, chats)

    assert result["assistantText"] != ""


async def test_창구가_없으면_draft를_보내지_않는다() -> None:
    chat = FakeToolLoopChat(["정리했습니다"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(chat_confirmation_response)) as client:
        result = await chat_mod.run_chat(
            _request(), client, ExecutionTrace(), CHAT_PROMPT, None, ChatPair(chat, None)
        )
    assert result["assistantText"] != ""


async def test_접수된_실행의_결과가_제안_쓰기를_실행_없이_담아_낸다() -> None:
    result, _posted = await _run(
        [
            [{"name": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}}],
            "task-1 아카이브를 제안했고 승인을 기다립니다.",
        ],
        readApiBaseUrl="http://tracer-api.test",
    )

    assert result["proposedWrites"] == [
        {
            "confirmationId": "conf-propose_task_write",
            "toolName": "propose_task_write",
            "args": {"action": "archive", "taskId": "task-1"},
        }
    ]
    assert result["assistantText"] != ""


def test_한_도구_호출은_조각이_여러_번_와도_한_줄만_남긴다() -> None:
    # 스트림은 한 호출을 여러 조각으로 나눠 주므로 조각마다 알리면 같은 도구가 여러 줄로 적힌다.
    announced: set[str] = set()
    partial = AIMessage(
        content="",
        tool_calls=[{"id": "call-1", "name": "search_tasks", "args": {}, "type": "tool_call"}],
    )
    complete = AIMessage(
        content="",
        tool_calls=[{"id": "call-1", "name": "search_tasks", "args": {"q": "배포"}, "type": "tool_call"}],
    )
    another = AIMessage(
        content="",
        tool_calls=[{"id": "call-2", "name": "get_task", "args": {}, "type": "tool_call"}],
    )

    assert _fresh_tool_names(partial, announced) == ["search_tasks"]
    assert _fresh_tool_names(complete, announced) == []
    assert _fresh_tool_names(another, announced) == ["get_task"]


async def test_첫_조각은_곧바로_보내고_그_뒤로만_묶는다() -> None:
    recorder = _Recorder()

    # 시계가 0에서 시작해도 첫 조각이 나가야 앞쪽 엣지다.
    interval = chat_draft_rules().interval_s
    # 간격이 계약에서 움직여도 안과 밖이 뒤바뀌지 않도록 눈금을 그 값에서 만든다.
    ticks = iter([0.0, interval / 15, interval * 2 / 15, interval * 4 / 3, interval * 2])
    publisher = DraftPublisher(recorder, lambda: next(ticks))
    if True:
        await publisher.push("정")
        await publisher.push("리")
        await publisher.push("했다")
        await publisher.flush()

    # 첫 조각은 곧바로, 간격 안에 들어온 나머지는 묶여서 나간다.
    assert [row["text"] for row in recorder.rows] == ["정", "정리했다"]
    # 순번은 보낸 횟수가 아니라 받은 조각을 센다.
    assert [row["draftSeq"] for row in recorder.rows] == [1, 3]
    assert [row["phase"] for row in recorder.rows] == ["responding", "responding"]


async def test_글자를_내지_않는_구간에도_무엇을_하는_중인지_보낸다() -> None:
    recorder = _Recorder()

    ticks = count(0.0, 1.0)
    publisher = DraftPublisher(recorder, lambda: next(ticks))
    if True:
        await publisher.push_tool("search_tasks")
        await publisher.mark_phase("thinking")
        await publisher.push("찾았다")
        await publisher.flush()

    # 도구 구간과 그 뒤의 사고 구간이 글자 없이도 화면에 닿는다.
    assert [row["phase"] for row in recorder.rows] == ["tool", "thinking", "responding"]


async def test_답변이_흐르는_동안에는_사고로_되돌리지_않는다() -> None:
    recorder = _Recorder()

    ticks = count(0.0, 1.0)
    publisher = DraftPublisher(recorder, lambda: next(ticks))
    if True:
        await publisher.push("답")
        await publisher.flush()
        await publisher.mark_phase("thinking")

    assert [row["phase"] for row in recorder.rows] == ["responding"]


async def _system_content(turns: list[Any], **overrides: Any) -> tuple[FakeToolLoopChat, list[Any]]:
    chat = FakeToolLoopChat(turns)
    chats = ChatPair(chat, None)
    req = _request(**overrides)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        await chat_mod.run_chat(req, client, ExecutionTrace(), CHAT_PROMPT, None, chats)
    return chat, list(chat.requests[0][0].content)


async def test_사실과_요약은_시스템_메시지_밖에_붙는다() -> None:
    chat, content = await _system_content(
        ["정리했습니다"],
        summary="앞선 대화 요약",
        facts=[{"key": "editor", "content": "vim을 쓴다"}],
    )

    assert "vim을 쓴다" not in str(content)
    assert "앞선 대화 요약" not in str(content)
    보낸것 = str([message.content for message in chat.requests[0]])
    assert "vim을 쓴다" in 보낸것 and "앞선 대화 요약" in 보낸것


async def test_사실이_늘어도_캐시되는_시스템_접두사는_그대로다() -> None:
    # 접두사 일치라 사실 하나가 시스템 메시지에 섞이면 remember_fact 한 번에 캐시가 통째로 끝난다.
    _first, before = await _system_content(["정리했습니다"], summary="요약 A", facts=[])
    _chat, after = await _system_content(
        [[{"name": "search_tasks", "args": {"query": "x"}}], "정리했습니다"],
        summary="요약 B",
        facts=[{"key": "editor", "content": "vim을 쓴다"}],
    )

    assert before == after


async def test_이전_대화가_있는_턴에서도_시스템_메시지는_선두에만_연속으로_온다() -> None:
    # 지난 이력의 human·ai 메시지 뒤에 맥락을 시스템으로 붙이면 Anthropic이 메시지 순서를 거절한다.
    chat = FakeToolLoopChat(["정리했습니다"])
    chats = ChatPair(chat, None)
    req = _request(
        messages=[
            {"role": "user", "content": "task-1 상태 알려줘"},
            {"role": "assistant", "content": "진행 중입니다"},
            {"role": "user", "content": "task-1 아카이브해줘"},
        ],
        summary="앞선 대화 요약",
        facts=[{"key": "editor", "content": "vim을 쓴다"}],
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as client:
        await chat_mod.run_chat(req, client, ExecutionTrace(), CHAT_PROMPT, None, chats)

    보낸것 = chat.requests[0]
    타입들 = [message.type for message in 보낸것]
    첫_비시스템_위치 = next(index for index, 타입 in enumerate(타입들) if 타입 != "system")
    assert "system" not in 타입들[첫_비시스템_위치:], f"system 메시지가 human·ai 뒤에 왔다: {타입들}"


async def test_창구가_느려도_토큰_소비를_막지_않는다() -> None:
    released = asyncio.Event()
    recorder = _Recorder(gate=released)

    ticks = count(0.0, 1.0)
    publisher = DraftPublisher(recorder, lambda: next(ticks))
    if True:
        await publisher.push("첫")
        # 창구가 응답하지 않아도 다음 조각을 계속 받는다.
        await publisher.push("둘")
        await publisher.push("셋")
        released.set()
        await publisher.flush()

    assert [row["text"] for row in recorder.rows][-1] == "첫둘셋"
