"""미들웨어 스택의 순서 의존이 네 에이전트에서 지켜지는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from tests.support.contract import shared_contract
from tracer_agent.worker.agents.chat.langchain_agent import chat_stack
from tracer_agent.worker.agents.runtime.llm import pacing
from tracer_agent.worker.agents.runtime.llm.middleware_stack import AgentMiddlewareStack

_TRANSIENT: tuple[type[Exception], ...] = (httpx.TransportError,)
_FALLBACK = GenericFakeChatModel(messages=iter([]))


def _pacing_with(*, calls: int, provider_backstop: int) -> Callable[[], dict[str, Any]]:
    """두 칸이 같은 값인 동안 가려지는 파생 근거를 드러내도록 계약의 두 칸을 갈라 놓는다."""
    declared = shared_contract("execution.budget.json")["pacing"]
    reserve = {**declared["landingReserve"], "calls": calls, "providerBackstop": provider_backstop}
    return lambda: {**declared, "landingReserve": reserve}


def _job_stack(*, fallback: bool = False, serializes_tools: bool = False) -> AgentMiddlewareStack:
    """구조화 출력을 요구하는 잡 에이전트가 세우는 스택이다."""
    return AgentMiddlewareStack(
        max_turns=4,
        transient_errors=_TRANSIENT,
        fallback_chat=_FALLBACK if fallback else None,
        repairs_structured_output=True,
        serializes_tools=serializes_tools,
        tool_failure_text="Tool {tool} failed: {reason}.",
    )


def _kinds(middleware: list[Any]) -> list[str]:
    """LangChain이 목록의 첫 항목을 가장 바깥으로 합성하므로 낮은 자리가 더 바깥이다."""
    return [type(one).__name__ for one in middleware]


def _stacks(*, fallback: bool = False) -> list[tuple[str, list[str]]]:
    """네 에이전트가 실제로 세우는 스택을 바깥에서 안쪽 순서의 이름 목록으로 낸다."""
    chat = chat_stack(_TRANSIENT, max_turns=4, fallback_chat=_FALLBACK if fallback else None)
    return [
        ("chat", _kinds(chat.build())),
        ("recipe-scan", _kinds(_job_stack(fallback=fallback).build())),
        ("task-cleanup", _kinds(_job_stack(fallback=fallback, serializes_tools=True).build())),
        ("title-suggestion", _kinds(_job_stack(fallback=fallback).build())),
    ]


class Test미들웨어순서:
    def test_캐시_경계가_남은_몫을_알리는_꼬리보다_안쪽에_선다(self) -> None:
        # 꼬리가 붙기 전에 서면 경계가 호출마다 바뀌는 그 꼬리 위에 놓여 다시 읽히지 않는다.
        for name, kinds in _stacks():
            assert kinds.index("StandardAgentMiddleware") < kinds.index("PromptCacheMiddleware"), name

    def test_맥락_정리가_비용_장부보다_바깥에_선다(self) -> None:
        for name, kinds in _stacks():
            assert kinds.index("ContextEditingMiddleware") < kinds.index("StandardAgentMiddleware"), name

    def test_도구_재시도가_모델_재시도보다_바깥에_선다(self) -> None:
        for name, kinds in _stacks():
            assert kinds.index("ToolRetryMiddleware") < kinds.index("ModelRetryMiddleware"), name

    def test_같은_모델_재시도가_대체_모델보다_안쪽에_선다(self) -> None:
        for name, kinds in _stacks(fallback=True):
            assert kinds.index("FallbackModelMiddleware") < kinds.index("ModelRetryMiddleware"), name

    def test_대체_모델이_없으면_그_미들웨어를_세우지_않는다(self) -> None:
        for name, kinds in _stacks():
            assert "FallbackModelMiddleware" not in kinds, name

    def test_네_에이전트가_같은_층을_같은_순서로_세운다(self) -> None:
        # 구조화 복구와 도구 실패 되돌림만 산출 형태에 따라 갈리고 나머지 층과 순서는 넷이 함께 쓴다.
        optional = {"StructuredOutputRepairMiddleware", "ToolFailureMiddleware"}
        for name, kinds in _stacks():
            assert [one for one in kinds if one not in optional] == [
                "TurnLimitMiddleware" if name == "chat" else "ModelCallLimitMiddleware",
                "ContextEditingMiddleware",
                "StandardAgentMiddleware",
                "PromptCacheMiddleware",
                "ToolRetryMiddleware",
                "ModelRetryMiddleware",
            ], name

    def test_도구_실패를_되돌리는_층이_도구_재시도보다_바깥에_선다(self) -> None:
        # 재시도가 소진되기 전에 문자열로 바뀌면 일시 오류가 다시 시도되지 않는다.
        by_name = dict(_stacks())
        assert "ToolFailureMiddleware" not in by_name["chat"]
        for name in ("recipe-scan", "task-cleanup", "title-suggestion"):
            kinds = by_name[name]
            assert kinds.index("ToolFailureMiddleware") < kinds.index("ToolRetryMiddleware"), name
            assert kinds.index("StandardAgentMiddleware") < kinds.index("ToolFailureMiddleware"), name

    def test_구조화_출력을_요구하는_잡만_산출을_다시_받는다(self) -> None:
        # 대화는 자유 텍스트를 내므로 스키마에 걸려 버려질 산출 자체가 없다.
        by_name = dict(_stacks())
        assert "StructuredOutputRepairMiddleware" not in by_name["chat"]
        for name in ("recipe-scan", "task-cleanup", "title-suggestion"):
            kinds = by_name[name]
            assert kinds.index("StructuredOutputRepairMiddleware") < kinds.index("StandardAgentMiddleware"), (
                name
            )

    def test_상한은_알리는_총량보다_마무리_호출_몫만큼_넉넉하다(self) -> None:
        headroom = shared_contract("execution.budget.json")["pacing"]["landingReserve"]["calls"]

        for stack in (chat_stack(_TRANSIENT, max_turns=4), _job_stack()):
            limit = next(one for one in stack.build() if isinstance(one, ModelCallLimitMiddleware))
            assert limit.run_limit == 4 + headroom

    def test_턴_몫의_근거는_calls_하나이고_달러_백스톱은_턴에_닿지_않는다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pacing, "_pacing", _pacing_with(calls=5, provider_backstop=9))

        for stack in (chat_stack(_TRANSIENT, max_turns=4), _job_stack()):
            limit = next(one for one in stack.build() if isinstance(one, ModelCallLimitMiddleware))
            assert limit.run_limit == 4 + 5

    def test_대화만_상한에서_그때까지의_답을_남기고_끝낸다(self) -> None:
        assert _kinds(chat_stack(_TRANSIENT, max_turns=4).build())[0] == "TurnLimitMiddleware"
        assert _kinds(_job_stack().build())[0] == "ModelCallLimitMiddleware"

    def test_대화의_도구는_공유_장부를_직렬화한다(self) -> None:
        standard = next(
            one
            for one in chat_stack(_TRANSIENT, max_turns=4).build()
            if type(one).__name__ == "StandardAgentMiddleware"
        )
        assert standard._serializes_tools  # noqa: SLF001
