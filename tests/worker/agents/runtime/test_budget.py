"""ToolLoopBudget이 실제 응답 모델로 과금하는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

import pytest

from tests.support.fakes import mk_ai, mk_rates
from tracer_agent.worker.agents.runtime.errors import BudgetExceeded
from tracer_agent.worker.agents.runtime.llm.budget import ExecutionBudget, ToolLoopBudget

_USAGE = {
    "input_tokens": 1_000_000,
    "output_tokens": 0,
    "total_tokens": 1_000_000,
    "input_token_details": {"cache_read": 0, "cache_creation": 0},
}


def test_실제_응답_모델로_단가를_매긴다() -> None:
    # sonnet 생성자로 열어도 응답이 haiku에서 왔으면 haiku 단가($1/1M input)로 매긴다.
    budget = ToolLoopBudget("agent", "claude-sonnet-4-6", 10.0, mk_rates())
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    budget.charge(message)

    assert budget.spent == pytest.approx(1.0)


def test_응답에_모델이_없으면_생성자_모델로_매긴다() -> None:
    budget = ToolLoopBudget("agent", "claude-sonnet-4-6", 10.0, mk_rates())
    message = mk_ai(usage=_USAGE, response_metadata={})

    budget.charge(message)

    assert budget.spent == pytest.approx(3.0)


def test_모르는_실제_모델이면_예산을_거부한다() -> None:
    budget = ToolLoopBudget("agent", "claude-sonnet-4-6", 10.0, mk_rates())
    message = mk_ai(usage=_USAGE, response_metadata={"model": "gpt-4o"})

    with pytest.raises(BudgetExceeded):
        budget.charge(message)


def test_실행의_서로_다른_루프가_하나의_달러_상한을_쓴다() -> None:
    execution = ExecutionBudget(1.5, mk_rates())
    first = execution.new_loop("triage", "claude-haiku-4-5")
    second = execution.new_loop("inspect", "claude-haiku-4-5")
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    first.charge(message)
    with pytest.raises(BudgetExceeded):
        second.charge(message)

    assert first.delta == pytest.approx(1.0)
    assert second.delta == pytest.approx(0.0)
    assert execution.spent == pytest.approx(1.0)


def test_착지한_루프의_결론_호출은_실행_상한에서도_끊기지_않는다() -> None:
    execution = ExecutionBudget(1.5, mk_rates())
    loop = execution.new_loop("synthesize", "claude-haiku-4-5")
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    loop.charge(message)
    loop.land()
    loop.charge(message)

    assert execution.spent == pytest.approx(2.0)


def test_조사_루프는_배정받은_상한을_넘지_않는다() -> None:
    execution = ExecutionBudget(2.0, mk_rates())
    inspect = execution.new_loop("inspect", "claude-haiku-4-5", max_cost_usd=0.5)
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    with pytest.raises(BudgetExceeded):
        inspect.charge(message)

    assert execution.spent == pytest.approx(0.0)
