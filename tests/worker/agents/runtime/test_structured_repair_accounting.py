"""스키마에 걸려 버려진 산출도 예산과 궤적에 실리는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field, PrivateAttr

from tests.support.fakes import mk_ai, mk_rates
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import ToolLoopBudget
from tracer_agent.worker.agents.runtime.llm.standard_agent import (
    StandardAgentContext,
    StandardAgentMiddleware,
)
from tracer_agent.worker.agents.runtime.llm.structured_repair import (
    StructuredOutputRepairMiddleware,
)

_MODEL = "claude-haiku-4-5"
# 공급자가 강제하지 못하는 제약 하나를 스키마에 둔다.
_LIMIT = 10


class _Draft(BaseModel):
    verdict: str = Field(max_length=_LIMIT)


class _ProviderModel(GenericFakeChatModel):
    """공급자 강제 경로를 밟도록 구조화 출력을 지원한다고 알리는 대역이다."""

    _replies: list[AIMessage] = PrivateAttr(default_factory=list)

    def bind_tools(self, _tools: Any, **_kwargs: Any) -> _ProviderModel:
        return self

    async def ainvoke(self, _input: Any, _config: Any = None, **_kwargs: Any) -> AIMessage:
        return self._replies.pop(0)


def _model(payloads: list[str]) -> _ProviderModel:
    model = _ProviderModel(messages=iter([]), profile={"structured_output": True})
    model._replies = [mk_ai(text) for text in payloads]  # noqa: SLF001
    return model


def _context(budget: ToolLoopBudget, trace: ExecutionTrace) -> StandardAgentContext:
    return StandardAgentContext(agent_name="test", trace=trace, budget=budget, max_model_turns=4)


def _agent(model: _ProviderModel) -> Any:
    return create_agent(
        model,
        tools=[],
        response_format=_Draft,
        # 거부된 산출도 장부를 지나야 하므로 다시 받는 자리가 더 바깥에 선다.
        middleware=[StructuredOutputRepairMiddleware(), StandardAgentMiddleware()],
        context_schema=StandardAgentContext,
    )


def _too_long() -> str:
    return json.dumps({"verdict": "열 글자를 확실히 넘기는 문장이다"}, ensure_ascii=False)


def _short() -> str:
    return json.dumps({"verdict": "짧다"}, ensure_ascii=False)


async def _spend(payloads: list[str]) -> tuple[float, ExecutionTrace, Any]:
    """주어진 응답으로 agent를 실행하고 그 실행이 청구한 달러와 궤적을 낸다."""
    trace = ExecutionTrace()
    budget = ToolLoopBudget("test", _MODEL, 1.0, mk_rates())
    result = await _agent(_model(payloads)).ainvoke({"messages": []}, context=_context(budget, trace))
    return budget.spent, trace, result


async def test_거부된_산출과_다시_받은_산출이_모두_청구된다() -> None:
    once, _trace, _result = await _spend([_short()])

    twice, trace, result = await _spend([_too_long(), _short()])

    assert result["structured_response"].verdict == "짧다"
    # 두 호출이 같은 사용량을 냈으므로 거부된 호출이 빠지면 한 번 분에서 멈춘다.
    assert twice == pytest.approx(once * 2)
    assert once > 0.0
    assert trace.turns == 2


async def test_거부된_산출만_남고_끝나도_그_호출이_청구된다() -> None:
    trace = ExecutionTrace()
    budget = ToolLoopBudget("test", _MODEL, 1.0, mk_rates())
    agent = _agent(_model([_too_long(), _too_long()]))

    with pytest.raises(Exception, match="Failed to parse structured output"):
        await agent.ainvoke({"messages": []}, context=_context(budget, trace))

    assert budget.spent > 0.0
    assert trace.turns == 2
