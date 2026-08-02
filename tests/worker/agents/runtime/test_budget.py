"""ToolLoopBudget이 실제 응답 모델로 과금하는지, 턴 원장이 계약의 셈을 내는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

import gc

import pytest

from tests.support.contract import agent_cases, shared_contract
from tests.support.fakes import mk_ai, mk_rates
from tracer_agent.worker.agents.runtime.errors import BudgetExceeded
from tracer_agent.worker.agents.runtime.llm.budget import (
    AgentBudgetLease,
    AgentBudgetSpend,
    ExecutionBudget,
    ToolLoopBudget,
)

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


def _recipe_scan_budget_cases() -> dict[str, object]:
    payload: dict[str, object] = agent_cases("recipe-scan")["executionBudget"]
    return payload


def test_예약이_계약이_적은_액수를_뗀_순서대로_낸다() -> None:
    reservation = shared_contract("execution.budget.json")["reservation"]
    case = _recipe_scan_budget_cases()["reservation"]
    assert reservation["order"] == ["repair", "survey", "synthesisFloor"]

    execution = ExecutionBudget(
        case["startingRemainingBudgetUsd"], mk_rates(), max_turns=case["startingRemainingTurns"]
    )
    repair_lease = execution.reserve(reservation["repair"]["turns"], reservation["repair"]["budgetShare"])
    survey_lease = execution.reserve(reservation["survey"]["turns"], reservation["survey"]["budgetShare"])
    synthesis_floor_lease = execution.reserve(
        reservation["synthesisFloor"]["turns"], reservation["synthesisFloor"]["budgetShare"]
    )

    _assert_lease(repair_lease, case["expect"]["repair"])
    _assert_lease(survey_lease, case["expect"]["survey"])
    _assert_lease(synthesis_floor_lease, case["expect"]["synthesisFloor"])
    assert execution.remaining_turns == case["expect"]["remainingAfterAll"]["turns"]
    assert execution.remaining_budget_usd == pytest.approx(case["expect"]["remainingAfterAll"]["budgetUsd"])


def _assert_lease(lease: AgentBudgetLease, expect: dict[str, object]) -> None:
    assert lease.max_turns == expect["grantedTurns"]
    assert lease.max_cost_usd == pytest.approx(expect["grantedUsd"])


@pytest.mark.parametrize(
    "case", _recipe_scan_budget_cases()["weightAllocation"]["cases"], ids=lambda c: c["name"]
)
def test_lease_many가_weight_배열마다_계약이_적은_턴과_달러를_낸다(case: dict[str, object]) -> None:
    execution = ExecutionBudget(case["availableUsd"], mk_rates(), max_turns=case["availableTurns"])

    leases = execution.lease_many(case["requestedTurns"], 1.0)

    assert [lease.max_turns for lease in leases] == case["expect"]["grantedTurns"]
    assert [lease.max_cost_usd for lease in leases] == pytest.approx(case["expect"]["grantedUsd"])


def test_사용량을_모르는_호출을_정산하면_떼어준_몫_전부가_빠진다() -> None:
    execution = ExecutionBudget(1.0, mk_rates(), max_turns=10)
    lease = execution.lease(1.0)
    assert lease.max_turns == 10
    assert lease.max_cost_usd == pytest.approx(1.0)

    execution.settle(lease, AgentBudgetSpend(cost_usd=None, num_turns=None))

    assert execution.remaining_turns == 0
    assert execution.remaining_budget_usd == pytest.approx(0.0)


def test_예약분은_정산에서_두_번_빠지지_않는다() -> None:
    case = _recipe_scan_budget_cases()["settlementWithoutReport"]
    execution = ExecutionBudget(
        case["startingRemainingBudgetUsd"], mk_rates(), max_turns=case["startingRemainingTurns"]
    )

    lease = execution.reserve(case["reserve"]["turns"], case["reserve"]["budgetShare"])
    assert execution.remaining_turns == case["afterReserve"]["remainingTurns"]
    assert execution.remaining_budget_usd == pytest.approx(case["afterReserve"]["remainingBudgetUsd"])

    execution.settle(
        lease, AgentBudgetSpend(cost_usd=case["settle"]["costUsd"], num_turns=case["settle"]["numTurns"])
    )

    assert execution.remaining_turns == case["expect"]["remainingTurns"]
    assert execution.remaining_budget_usd == pytest.approx(case["expect"]["remainingBudgetUsd"])


def test_전문가가_예산을_소진해도_종합과_수리의_몫은_예약대로_남는다() -> None:
    """repair·synthesisFloor 예약은 팬아웃이 잔량을 전부 태워도 침범당하지 않는다."""
    reservation = shared_contract("execution.budget.json")["reservation"]
    execution = ExecutionBudget(2.0, mk_rates(), max_turns=15)
    repair_lease = execution.reserve(reservation["repair"]["turns"], reservation["repair"]["budgetShare"])
    execution.reserve(reservation["survey"]["turns"], reservation["survey"]["budgetShare"])
    synthesis_floor_lease = execution.reserve(
        reservation["synthesisFloor"]["turns"], reservation["synthesisFloor"]["budgetShare"]
    )

    # 전문가 팬아웃이 남은 잔량 전부를 요청해 소진한다.
    probe_leases = execution.lease_many([10, 10, 10], 1.0)
    for lease in probe_leases:
        execution.settle(lease, AgentBudgetSpend(cost_usd=None, num_turns=None))

    assert execution.remaining_turns == 0
    assert execution.remaining_budget_usd == pytest.approx(0.0)
    # 이미 뗀 리스는 나중의 소진과 무관하게 처음 받은 턴과 달러를 그대로 쥔다.
    assert repair_lease.max_turns == reservation["repair"]["turns"]
    assert repair_lease.max_cost_usd > 0
    assert synthesis_floor_lease.max_turns == reservation["synthesisFloor"]["turns"]


class Test리스식별자:
    def test_회수된_리스의_열쇠가_다음_리스를_오염시키지_않는다(self) -> None:
        budget = ExecutionBudget(1.0, mk_rates(), max_turns=10)
        first = budget.reserve(2, 0.2)
        first_id = first.lease_id
        budget.settle(first, AgentBudgetSpend(cost_usd=0.1, num_turns=1))
        del first
        gc.collect()

        second = budget.reserve(2, 0.2)

        assert second.lease_id != first_id
        budget.settle(second, AgentBudgetSpend(cost_usd=0.1, num_turns=1))

    def test_같은_몫의_리스_둘이_서로_다른_열쇠를_갖는다(self) -> None:
        budget = ExecutionBudget(1.0, mk_rates(), max_turns=10)

        left = budget.reserve(2, 0.2)
        right = budget.reserve(2, 0.2)

        assert left.lease_id != right.lease_id
        budget.settle(left, AgentBudgetSpend(cost_usd=0.0, num_turns=0))
        budget.settle(right, AgentBudgetSpend(cost_usd=0.0, num_turns=0))

    def test_합친_리스가_자기_열쇠로_예약분을_한_번만_되돌린다(self) -> None:
        budget = ExecutionBudget(1.0, mk_rates(), max_turns=10)
        floor = budget.reserve(3, 0.0)
        remaining_before = budget.remaining_turns

        combined = budget.combine([floor, AgentBudgetLease(max_turns=2, max_cost_usd=0.0)])
        budget.settle(combined, AgentBudgetSpend(cost_usd=0.0, num_turns=0))

        assert budget.remaining_turns == remaining_before + 3
