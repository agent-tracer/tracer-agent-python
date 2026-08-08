"""리페어가 청구와 상한과 원장에 어떻게 세어지는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, PrivateAttr

from tests.support.fakes import mk_ai, mk_rates
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import SharedToolLoopBudget, single_loop_budget
from tracer_agent.worker.agents.runtime.llm.standard_agent import (
    StandardAgentContext,
    StandardAgentMiddleware,
)
from tracer_agent.worker.agents.runtime.llm.structured_agent import (
    StructuredAgentResult,
    invoke_structured_agent,
    output_tool,
)
from tracer_agent.worker.agents.runtime.llm.structured_repair import (
    StructuredOutputRepairMiddleware,
)

_MODEL = "claude-haiku-4-5"
# 공급자가 강제하지 못하는 제약 하나를 스키마에 둔다.
_LIMIT = 10
_MAX_TURNS = 4


class _Draft(BaseModel):
    verdict: str = Field(max_length=_LIMIT)


class _ToolModel(GenericFakeChatModel):
    """산출을 도구로 부르는 프로덕션 경로를 밟도록 도구 호출만 돌려주는 대역이다."""

    _replies: list[AIMessage] = PrivateAttr(default_factory=list)

    def bind_tools(self, _tools: Any, **_kwargs: Any) -> _ToolModel:
        return self

    async def ainvoke(self, _input: Any, _config: Any = None, **_kwargs: Any) -> AIMessage:
        return self._replies.pop(0)


class _CountingTurnLimit(ModelCallLimitMiddleware[Any, Any]):
    """실제 상한 미들웨어가 노드마다 올리는 수를 테스트가 그대로 읽게 한다."""

    def __init__(self, run_limit: int) -> None:
        super().__init__(run_limit=run_limit, exit_behavior="error")
        self.counted = 0

    def after_model(self, state: Any, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self.counted += 1
        return super().after_model(state, runtime)


def _output_call(verdict: str, index: int) -> AIMessage:
    return mk_ai(tool_calls=[{"name": _Draft.__name__, "args": {"verdict": verdict}, "id": f"call-{index}"}])


def _model(verdicts: list[str]) -> _ToolModel:
    model = _ToolModel(messages=iter([]))
    model._replies = [_output_call(verdict, index) for index, verdict in enumerate(verdicts)]  # noqa: SLF001
    return model


def _context(budget: SharedToolLoopBudget, trace: ExecutionTrace) -> StandardAgentContext:
    return StandardAgentContext(agent_name="test", trace=trace, budget=budget, max_model_turns=_MAX_TURNS)


def _agent(model: _ToolModel, limit: _CountingTurnLimit) -> Any:
    return create_agent(
        model,
        tools=[],
        response_format=output_tool(_Draft),
        # 거부된 산출도 장부를 지나야 하므로 다시 받는 자리가 더 바깥에 선다.
        middleware=[limit, StructuredOutputRepairMiddleware(), StandardAgentMiddleware()],
        context_schema=StandardAgentContext,
    )


_TOO_LONG = "열 글자를 확실히 넘기는 문장이다"
_SHORT = "짧다"


async def _run(verdicts: list[str]) -> tuple[SharedToolLoopBudget, ExecutionTrace, _CountingTurnLimit, Any]:
    """주어진 산출로 agent를 실행하고 청구와 궤적과 상한 카운터와 결과를 함께 낸다."""
    trace = ExecutionTrace()
    budget = single_loop_budget("test", _MODEL, 1.0, mk_rates())
    limit = _CountingTurnLimit(run_limit=_MAX_TURNS)
    context = _context(budget, trace)
    result = await invoke_structured_agent(
        _agent(_model(verdicts), limit),
        messages=[],
        context=context,
        response_type=_Draft,
        recursion_limit=10 * _MAX_TURNS,
        missing_response="draft is missing",
    )
    return budget, trace, limit, (context, result)


async def test_거부된_산출과_다시_받은_산출이_모두_청구된다() -> None:
    once, _trace, _limit, _out = await _run([_SHORT])

    twice, trace, _limit, (_context, result) = await _run([_TOO_LONG, _SHORT])

    assert result.response.verdict == _SHORT
    # 두 호출이 같은 사용량을 냈으므로 거부된 호출이 빠지면 한 번 분에서 멈춘다.
    assert twice.spent == pytest.approx(once.spent * 2)
    assert once.spent > 0.0
    assert trace.turns == 2


async def test_거부된_산출만_남고_끝나도_그_호출이_청구된다() -> None:
    with pytest.raises(Exception, match="Failed to parse structured output"):
        await _run([_TOO_LONG, _TOO_LONG])


async def test_리페어를_겪어도_턴을_세는_세_자리가_같은_수를_낸다() -> None:
    _budget, _trace, limit, (context, result) = await _run([_TOO_LONG, _SHORT])

    assert isinstance(result, StructuredAgentResult)
    # 페이싱 문구가 쓰는 수와 실제로 끊는 수와 예산 원장이 빼는 수가 같은 단위여야 한다.
    assert context.turns_seen == limit.counted
    assert context.turns_seen == result.num_turns


async def test_페이싱_문구가_알리는_턴은_몫을_넘지_않는다() -> None:
    _budget, _trace, _limit, (context, _result) = await _run([_TOO_LONG, _SHORT])

    assert context.turns_seen <= context.max_model_turns
