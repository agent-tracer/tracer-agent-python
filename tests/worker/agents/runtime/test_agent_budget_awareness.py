"""도구 예산을 다 쓴 실행이 결론을 내고 끝나는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, mk_ai
from tests.support.prompts import CHAT_PROMPT, CONTRACT_VERSION, TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.runner import execute
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.runtime.llm.standard_agent import (
    FINALIZE_STRUCTURED_DIRECTIVE,
    FINALIZE_TEXT_DIRECTIVE,
    finalize_directive,
)
from tracer_agent.worker.agents.task_cleanup import agent as cleanup_mod

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-1"}

# 호출 하나가 sonnet 단가로 약 $0.21이라 세 번째 호출은 $0.50 상한 안에 들어갈 수 없다.
_EXPENSIVE_USAGE = {
    "input_tokens": 10_000,
    "output_tokens": 12_000,
    "total_tokens": 22_000,
    "input_token_details": {"cache_read": 0, "cache_creation": 0},
}

_DRAFT = {
    "suggestions": [
        {
            "kind": "archive",
            "taskId": "task-1",
            "rationale": "의미 있는 활동이 없다",
            "evidenceEventIds": [],
        }
    ]
}


class GreedyChat:
    """예산을 안 보고 계속 도구만 부르다가 결론 요구를 받으면 그때 출력하는 검토자 모델이다."""

    def __init__(self, usage: dict[str, Any] | None = None) -> None:
        self.bound_tools: list[Any] = []
        self.tools_per_call: list[list[str]] = []
        self.notices: list[str] = []
        self.usage = usage
        self._triage_listed = False

    def bind_tools(self, tools: list[Any], **_kwargs: Any) -> GreedyChat:
        self.bound_tools = tools
        return self

    def bind(self, **_kwargs: Any) -> GreedyChat:
        return self

    def with_structured_output(self, _schema: Any, **_kwargs: Any) -> Any:
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        names = [getattr(tool, "name", "") for tool in self.bound_tools]
        # 선별자는 후보를 한 번 훑고 검토자에게 후보 하나를 배정한다.
        if "TriagePlan" in names:
            if not self._triage_listed:
                self._triage_listed = True
                return mk_ai(
                    tool_calls=[
                        {"name": "list_candidate_tasks", "args": {}, "id": "call-list", "type": "tool_call"}
                    ]
                )
            return mk_ai(
                tool_calls=[
                    {
                        "name": "TriagePlan",
                        "args": {"inspect": [{"taskId": "task-1", "weight": 1}]},
                        "id": "call-triage",
                        "type": "tool_call",
                    }
                ]
            )
        # 조율자는 도구가 없으니 검토자 보고만 보고 곧바로 초안을 낸다.
        if "CleanupDraft" in names:
            return mk_ai(
                tool_calls=[{"name": "CleanupDraft", "args": _DRAFT, "id": "call-out", "type": "tool_call"}]
            )
        # 검토자는 예산을 안 보고 계속 이벤트만 읽다가 결론 요구를 받으면 그때 판정을 올린다.
        self.tools_per_call.append(names)
        directives = [
            message.content
            for message in messages
            if isinstance(message, HumanMessage) and isinstance(message.content, str)
        ]
        self.notices.append(directives[-1])
        if FINALIZE_STRUCTURED_DIRECTIVE in directives[-1]:
            return mk_ai(
                tool_calls=[
                    {
                        "name": "InspectReport",
                        "args": {
                            "taskId": "task-1",
                            "archivable": True,
                            "reason": "의미 있는 활동이 없다",
                            "citedEventIds": [],
                        },
                        "id": "call-report",
                        "type": "tool_call",
                    }
                ],
                usage=self.usage,
            )
        return mk_ai(
            tool_calls=[
                {
                    "name": "get_task_events",
                    "args": {"taskId": "task-1"},
                    "id": f"call-{len(self.tools_per_call)}",
                    "type": "tool_call",
                }
            ],
            usage=self.usage,
        )


_CANDIDATE = {
    "id": "task-1",
    "visibleTitle": "제목",
    "status": "running",
    "lastEventAt": None,
    "hasEvents": False,
    "activeChildCount": 0,
    "candidateReasons": ["stale"],
}


def _request() -> TaskCleanupRequest:
    return TaskCleanupRequest(
        model="claude-sonnet-4-6",
        apiKey="sk-test",
        modelRates=WIRE_MODEL_RATES,
        limits={**WIRE_LIMITS, "budgetUsd": 0.5},
        scannedAt="2026-07-14T00:00:00Z",
        userId="user-1",
        maxSuggestions=5,
        language="ko",
        batch={"candidates": [_CANDIDATE], "batchTruncated": False},  # type: ignore[arg-type]
        completionCallback=_COMPLETION,  # type: ignore[arg-type]
    )


async def _run(chat: GreedyChat, ledger: FakeTracerApi) -> Any:
    req = _request()
    chats = ChatPair(chat, None)  # type: ignore[arg-type]
    return await execute(
        "task-cleanup",
        req.model,
        req.deadlineMs,
        lambda usage: cleanup_mod.run_task_cleanup(req, ledger, usage, TASK_CLEANUP_PROMPT, None, chats),  # type: ignore[arg-type],
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )


async def test_예산을_다_써도_모은_근거로_결론을_낸다() -> None:
    chat = GreedyChat(usage=_EXPENSIVE_USAGE)
    ledger = FakeTracerApi()

    res = await _run(chat, ledger)

    assert res.error is None
    assert res.data["suggestions"] == _DRAFT["suggestions"]


async def test_턴_사용량을_매_턴_알려준다() -> None:
    chat = GreedyChat()

    await _run(chat, FakeTracerApi())

    # 실제 종료 게이트는 달러지만 모델의 self-pacing 신호는 턴 단위로 매 턴 갱신된다.
    assert "used 0 of" in chat.notices[0] and "tool-calling turns" in chat.notices[0]
    assert "used 1 of" in chat.notices[1]


async def test_비용_상한에_닿기_전에_결론을_받아낸다() -> None:
    chat = GreedyChat(usage=_EXPENSIVE_USAGE)

    res = await _run(chat, FakeTracerApi())

    assert res.error is None
    assert res.data["suggestions"] == _DRAFT["suggestions"]
    assert len(chat.notices) < 5
    assert FINALIZE_STRUCTURED_DIRECTIVE in chat.notices[-1]


async def test_예산이_바닥나면_조사_도구를_거두고_출력만_남긴다() -> None:
    chat = GreedyChat(usage=_EXPENSIVE_USAGE)

    await _run(chat, FakeTracerApi())

    assert "get_task_events" in chat.tools_per_call[0]
    assert chat.tools_per_call[-1] == ["InspectReport"]


async def test_착지했는지를_응답에_실어_보낸다() -> None:
    expensive = GreedyChat(usage=_EXPENSIVE_USAGE)
    landed = await _run(expensive, FakeTracerApi())

    cheap = GreedyChat()
    unlanded = await _run(cheap, FakeTracerApi())

    # 턴을 다 써 끝난 실행과 예산이 다해 종료한 실행을 서버가 구분해 답할 수 있어야 한다.
    assert landed.landed is True
    assert unlanded.landed is False


def test_마무리_지시는_최종_산출_형태로_갈린다() -> None:
    structured = finalize_directive(structured_output=True)
    free_text = finalize_directive(structured_output=False)

    assert "structured output" in structured
    # chat은 구조화 출력을 내지 않으므로 이 문구가 새면 모델이 없는 형식을 만들어 낸다.
    assert "structured" not in free_text
    assert "final answer" in free_text
    assert structured.startswith("The investigation budget is exhausted.")
    assert free_text.startswith("The investigation budget is exhausted.")


class _GreedyConversation:
    """예산을 안 보고 도구만 부르다가 마무리 요구를 받으면 그때 답을 쓰는 대화 대역이다."""

    def __init__(self) -> None:
        self.notices: list[str] = []

    def bind_tools(self, _tools: list[Any], **_kwargs: Any) -> _GreedyConversation:
        return self

    def bind(self, **_kwargs: Any) -> _GreedyConversation:
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        directives = [
            message.content
            for message in messages
            if isinstance(message, HumanMessage) and isinstance(message.content, str)
        ]
        self.notices.append(directives[-1])
        if FINALIZE_TEXT_DIRECTIVE in directives[-1]:
            return mk_ai(content="여기까지 확인한 내용입니다", usage=_EXPENSIVE_USAGE)
        return mk_ai(
            tool_calls=[
                {"name": "list_tags", "args": {}, "id": f"c{len(self.notices)}", "type": "tool_call"}
            ],
            usage=_EXPENSIVE_USAGE,
        )


async def test_대화는_예산이_바닥나도_자유_텍스트로_끝내라는_지시를_받는다() -> None:
    conversation = _GreedyConversation()
    chats = ChatPair(conversation, None)
    request = ChatRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": {**WIRE_LIMITS, "budgetUsd": 0.5},
            "threadId": "thread-1",
            "executionId": "execution-1",
            "userId": "user-1",
            "language": "ko",
            "messages": [{"role": "user", "content": "전부 찾아줘"}],
        }
    )

    result = await chat_mod.run_chat(request, None, ExecutionTrace(), CHAT_PROMPT, None, chats)  # type: ignore[arg-type]

    assert FINALIZE_TEXT_DIRECTIVE in conversation.notices[-1]
    assert FINALIZE_STRUCTURED_DIRECTIVE not in conversation.notices[-1]
    assert result["assistantText"] == "여기까지 확인한 내용입니다"
