"""단일 루프 장부가 실제 응답 모델로 과금하는지, 턴 원장이 계약의 셈을 내는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.contract import agent_cases, shared_contract
from tests.support.fakes import mk_ai, mk_rates
from tracer_agent.shared.agents.shared.graph_state import remaining_cost_usd, remaining_turns
from tracer_agent.worker.agents.runtime.errors import BudgetExceeded
from tracer_agent.worker.agents.runtime.llm.budget import (
    AgentBudgetLease,
    ExecutionBudget,
    lease_shares,
    pool_spend,
    reserved_spend,
    single_loop_budget,
)

_USAGE = {
    "input_tokens": 1_000_000,
    "output_tokens": 0,
    "total_tokens": 1_000_000,
    "input_token_details": {"cache_read": 0, "cache_creation": 0},
}


def test_실제_응답_모델로_단가를_매긴다() -> None:
    # sonnet 생성자로 열어도 응답이 haiku에서 왔으면 haiku 단가($1/1M input)로 매긴다.
    budget = single_loop_budget("agent", "claude-sonnet-4-6", 10.0, mk_rates())
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    budget.charge(message)

    assert budget.spent == pytest.approx(1.0)


def test_응답에_모델이_없으면_생성자_모델로_매긴다() -> None:
    budget = single_loop_budget("agent", "claude-sonnet-4-6", 10.0, mk_rates())
    message = mk_ai(usage=_USAGE, response_metadata={})

    budget.charge(message)

    assert budget.spent == pytest.approx(3.0)


def test_모르는_실제_모델이면_예산을_거부한다() -> None:
    budget = single_loop_budget("agent", "claude-sonnet-4-6", 10.0, mk_rates())
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

    # 상한에서 끊긴 호출도 공급자가 이미 답했으므로 그 비용이 두 장부에서 사라지지 않는다.
    assert first.delta == pytest.approx(1.0)
    assert second.delta == pytest.approx(1.0)
    assert execution.spent == pytest.approx(2.0)


def test_착지한_루프의_결론_호출은_실행_상한에서도_끊기지_않는다() -> None:
    execution = ExecutionBudget(1.5, mk_rates())
    loop = execution.new_loop("synthesize", "claude-haiku-4-5")
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    loop.charge(message)
    loop.land()
    loop.charge(message)

    assert execution.spent == pytest.approx(2.0)


def test_착지한_루프도_마무리_몫을_넘기면_끊긴다() -> None:
    # 종료한 뒤를 무한히 열면 계약이 landingReserve 로 정한 예약의 뜻이 사라진다.
    reserve = shared_contract("execution.budget.json")["pacing"]["landingReserve"]["calls"]
    execution = ExecutionBudget(1.5, mk_rates())
    loop = execution.new_loop("synthesize", "claude-haiku-4-5")
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    loop.charge(message)
    loop.land()
    for _ in range(reserve):
        loop.charge(message)
    with pytest.raises(BudgetExceeded):
        loop.charge(message)

    # 호출 하나가 $1.0이므로 상한 $1.5 위로 열리는 여유는 최고 호출의 calls 배뿐이다.
    assert execution.spent == pytest.approx(1.0 + reserve + 1.0)


def test_착지한_루프도_공급자_백스톱_위로는_열리지_않는다() -> None:
    # 마무리 몫은 가장 비쌌던 호출에 비례해 열리므로, 비싼 호출 하나가 그 여유를 폭주로 키운다.
    backstop = shared_contract("execution.budget.json")["pacing"]["landingReserve"]["providerBackstop"]
    execution = ExecutionBudget(1.0, mk_rates())
    loop = execution.new_loop("synthesize", "claude-haiku-4-5")
    message = mk_ai(
        usage={**_USAGE, "input_tokens": 900_000}, response_metadata={"model": "claude-haiku-4-5"}
    )

    loop.charge(message)
    loop.land()
    loop.charge(message)
    with pytest.raises(BudgetExceeded):
        loop.charge(message)

    assert execution.spent > 1.0 * backstop


def test_조사_루프는_배정받은_상한을_넘지_않는다() -> None:
    execution = ExecutionBudget(2.0, mk_rates())
    inspect = execution.new_loop("inspect", "claude-haiku-4-5", max_cost_usd=0.5)
    message = mk_ai(usage=_USAGE, response_metadata={"model": "claude-haiku-4-5"})

    with pytest.raises(BudgetExceeded):
        inspect.charge(message)

    assert execution.spent == pytest.approx(1.0)


def _recipe_scan_budget_cases() -> dict[str, Any]:
    payload: dict[str, Any] = agent_cases("recipe-scan")["executionBudget"]
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


def _assert_lease(lease: AgentBudgetLease, expect: dict[str, Any]) -> None:
    assert lease.max_turns == expect["grantedTurns"]
    assert lease.max_cost_usd == pytest.approx(expect["grantedUsd"])


@pytest.mark.parametrize(
    "case", _recipe_scan_budget_cases()["weightAllocation"]["cases"], ids=lambda c: c["name"]
)
def test_팬아웃_배분이_weight_배열마다_계약이_적은_턴과_달러를_낸다(case: dict[str, Any]) -> None:
    # 실행이 실제로 부르는 배분 함수이며 팬아웃은 이 결과를 그대로 전문가의 상한으로 싣는다.
    leases = lease_shares(case["requestedTurns"], case["availableTurns"], case["availableUsd"])

    assert [lease.max_turns for lease in leases] == case["expect"]["grantedTurns"]
    assert [lease.max_cost_usd for lease in leases] == pytest.approx(case["expect"]["grantedUsd"])


def _pool_state(turns: int, usd: float) -> dict[str, Any]:
    return {"max_turns": turns, "max_cost_usd": usd}


def test_사용량을_모르는_호출은_떼어준_몫_전부를_쓴_것으로_적는다() -> None:
    # 팬아웃 노드가 실패하면 실제 턴을 모르므로 배분받은 턴 전부를 풀 소모로 낸다.
    lease = lease_shares([10], 10, 1.0)[0]
    state = _pool_state(10, 1.0)

    spend = pool_spend(cost_usd=lease.max_cost_usd, turns_used=lease.max_turns)
    settled = {**state, **spend}

    assert remaining_turns(settled) == 0
    assert remaining_cost_usd(settled) == pytest.approx(0.0)


def test_예약분은_정산에서_두_번_빠지지_않는다() -> None:
    case = _recipe_scan_budget_cases()["settlementWithoutReport"]
    execution = ExecutionBudget(
        case["startingRemainingBudgetUsd"], mk_rates(), max_turns=case["startingRemainingTurns"]
    )

    lease = execution.reserve(case["reserve"]["turns"], case["reserve"]["budgetShare"])
    assert execution.remaining_turns == case["afterReserve"]["remainingTurns"]
    assert execution.remaining_budget_usd == pytest.approx(case["afterReserve"]["remainingBudgetUsd"])

    # 예약 리스로 돈 호출은 몫 전부를 쓰고도 팬아웃 풀을 건드리지 않는다.
    state = _pool_state(execution.remaining_turns, execution.remaining_budget_usd)
    settled = {
        **state,
        **reserved_spend(cost_usd=lease.max_cost_usd, turns_used=lease.max_turns),
    }

    assert remaining_turns(settled) == case["expect"]["remainingTurns"]
    assert remaining_cost_usd(settled) == pytest.approx(case["expect"]["remainingBudgetUsd"])


def test_전문가가_예산을_소진해도_종합과_수리의_몫은_예약대로_남는다() -> None:
    """repair·synthesisFloor 예약은 팬아웃이 잔량을 전부 소진해도 침범당하지 않는다."""
    reservation = shared_contract("execution.budget.json")["reservation"]
    execution = ExecutionBudget(2.0, mk_rates(), max_turns=15)
    repair_lease = execution.reserve(reservation["repair"]["turns"], reservation["repair"]["budgetShare"])
    execution.reserve(reservation["survey"]["turns"], reservation["survey"]["budgetShare"])
    synthesis_floor_lease = execution.reserve(
        reservation["synthesisFloor"]["turns"], reservation["synthesisFloor"]["budgetShare"]
    )

    # 전문가 팬아웃이 남은 잔량 전부를 요청해 소진한다.
    state = _pool_state(execution.remaining_turns, execution.remaining_budget_usd)
    for lease in lease_shares([10, 10, 10], execution.remaining_turns, execution.remaining_budget_usd):
        state = {
            **state,
            "pool_turns_used": state.get("pool_turns_used", 0) + lease.max_turns,
            "pool_cost_usd": state.get("pool_cost_usd", 0.0) + lease.max_cost_usd,
        }

    assert remaining_turns(state) == 0
    assert remaining_cost_usd(state) == pytest.approx(0.0)
    # 이미 뗀 리스는 나중의 소진과 무관하게 처음 받은 턴과 달러를 그대로 가진다.
    assert repair_lease.max_turns == reservation["repair"]["turns"]
    assert repair_lease.max_cost_usd > 0
    assert synthesis_floor_lease.max_turns == reservation["synthesisFloor"]["turns"]
