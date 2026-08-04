"""이어받은 실행이 앞선 시도의 지출을 이어받아 시작하는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from tests.support.fakes import WIRE_MODEL_RATES
from tracer_agent.worker.agents.runtime.durable_graph import prior_spend, resume_input
from tracer_agent.worker.agents.runtime.llm.budget import ExecutionBudget
from tracer_agent.worker.agents.runtime.pricing import ModelRates


class _State(TypedDict):
    model_cost_usd: float
    model_turns_used: int


async def _spend(_state: _State) -> dict[str, Any]:
    return {"model_cost_usd": 0.75, "model_turns_used": 4}


def _graph() -> StateGraph[Any, Any, Any, Any]:
    builder: StateGraph[Any, Any, Any, Any] = StateGraph(_State)
    builder.add_node("spend", _spend)
    builder.add_edge(START, "spend")
    builder.add_edge("spend", END)
    return builder


async def test_앞선_시도가_남긴_지출을_체크포인트에서_읽는다() -> None:
    saver = InMemorySaver()
    graph = _graph().compile(checkpointer=saver)
    config: Any = {"configurable": {"thread_id": "job-1"}}
    await graph.ainvoke({"model_cost_usd": 0.0, "model_turns_used": 0}, config=config)

    prior = await prior_spend(graph, config, saver)

    assert prior.resumed is True
    assert prior.cost_usd == pytest.approx(0.75)
    assert prior.turns == 4


async def test_이어갈_상태가_있으면_입력을_다시_넣지_않는다() -> None:
    saver = InMemorySaver()
    graph = _graph().compile(checkpointer=saver)
    config: Any = {"configurable": {"thread_id": "job-2"}}
    initial = {"model_cost_usd": 0.0, "model_turns_used": 0}
    await graph.ainvoke(initial, config=config)

    prior = await prior_spend(graph, config, saver)

    assert resume_input(initial, prior) is None


async def test_보존하지_않는_실행은_처음부터_센다() -> None:
    graph = _graph().compile()
    initial = {"model_cost_usd": 0.0, "model_turns_used": 0}

    prior = await prior_spend(graph, {}, None)

    assert prior.resumed is False
    assert prior.cost_usd == 0.0
    assert prior.turns == 0
    assert resume_input(initial, prior) is initial


def test_이어받은_예산이_상한을_처음부터_다시_열지_않는다() -> None:
    # 상한이 시도마다 다시 열리면 실행 하나가 정해진 몫을 여러 배 쓴다.
    budget = ExecutionBudget(2.0, ModelRates(WIRE_MODEL_RATES), max_turns=10, spent_usd=1.5, turns_used=7)

    assert budget.spent == pytest.approx(1.5)
    assert budget.remaining_budget_usd == pytest.approx(0.5)
    assert budget.remaining_turns == 3


def test_앞선_지출이_없으면_실행이_받은_몫을_그대로_갖는다() -> None:
    budget = ExecutionBudget(2.0, ModelRates(WIRE_MODEL_RATES), max_turns=10)

    assert budget.spent == 0.0
    assert budget.remaining_budget_usd == pytest.approx(2.0)
    assert budget.remaining_turns == 10
