"""세 잡이 지출을 적는 규약과 실행 총량이 예약을 두 번 세지 않는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat, mk_rates
from tests.support.prompts import (
    RECIPE_SCAN_PROMPT,
    TASK_CLEANUP_PROMPT,
    TITLE_SUGGESTION_PROMPT,
)
from tracer_agent.shared.agents.recipe_scan.models import (
    RecipeScanRequest,
    initial_recipe_scan_state,
)
from tracer_agent.shared.agents.shared.graph_state import (
    SpendChannels,
    remaining_cost_usd,
    remaining_turns,
)
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionRequest
from tracer_agent.worker.agents.recipe_scan.agent import RECIPE_SCAN_JOB
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.durable_graph import PriorSpend
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import (
    AgentBudgetLease,
    ExecutionBudget,
    combine_leases,
    floor_lease,
    floor_then_pool_spend,
    pool_spend,
    reserved_spend,
)
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.runtime.pricing import ModelRates
from tracer_agent.worker.agents.shared.execution_reservation import load_reservation_policy
from tracer_agent.worker.agents.task_cleanup.agent import TASK_CLEANUP_JOB
from tracer_agent.worker.agents.title_suggestion.agent import TITLE_SUGGESTION_JOB

MAX_TURNS = int(WIRE_LIMITS["maxTurns"])
BUDGET_USD = float(WIRE_LIMITS["budgetUsd"])
# 계약이 정한 세 예약을 뗀 뒤 팬아웃이 나눠 쓰는 몫이며 예약 순서까지 계약이 갖는다.
POOL_TURNS = 8
POOL_USD = 1.44
_SPEND_KEYS = tuple(SpendChannels.__annotations__)

_FRESH = PriorSpend(resumed=False, cost_usd=0.0, turns=0)

_TITLE_CONTEXT = {
    "title": "Untitled",
    "status": "completed",
    "totalEventCount": 3,
    "totalTurnCount": 1,
    "truncated": False,
    "turns": [{"turnIndex": 0, "askedText": "토큰 누수를 고쳐줘"}],
}

_ENVELOPE: dict[str, Any] = {
    "model": "claude-sonnet-4-6",
    "apiKey": "sk-test",
    "modelRates": WIRE_MODEL_RATES,
    "limits": WIRE_LIMITS,
    "userId": "user-1",
}


def _recipe_request() -> RecipeScanRequest:
    return RecipeScanRequest.model_validate({**_ENVELOPE, "taskId": "task-1", "language": "ko"})


def _cleanup_request() -> TaskCleanupRequest:
    return TaskCleanupRequest.model_validate(
        {
            **_ENVELOPE,
            "scannedAt": "2026-07-14T00:00:00Z",
            "maxSuggestions": 5,
            "language": "ko",
            "batch": {"candidates": []},
        }
    )


def _title_request() -> TitleSuggestionRequest:
    return TitleSuggestionRequest.model_validate(
        {**_ENVELOPE, "jobId": "job-1", "taskId": "task-1", "context": _TITLE_CONTEXT}
    )


def _composed(job: Any, req: Any, prompt: Any, chat: FakeToolLoopChat) -> Any:
    """프로덕션과 같은 compose로 예약을 떼고 이 실행의 노드를 세운다."""
    return job.compose(req, FakeTracerApi(), ExecutionTrace(), prompt, ChatPair(chat, None), _FRESH)


def _accumulate(state: dict[str, Any], update: Any) -> dict[str, Any]:
    """상태가 선언한 리듀서대로 지출 채널만 누적으로 합치고 나머지는 마지막 쓰기로 덮는다."""
    merged = {**state, **{key: value for key, value in update.items() if key not in _SPEND_KEYS}}
    for key in _SPEND_KEYS:
        merged[key] = state.get(key, 0) + update.get(key, 0)
    return merged


async def test_예약_리스로_돈_조율자는_팬아웃_풀을_깎지_않는다() -> None:
    # 예약분을 풀에서 다시 빼면 계약이 전문가에게 주기로 한 8턴이 5턴으로 줄어든다.
    plan = _composed(RECIPE_SCAN_JOB, _recipe_request(), RECIPE_SCAN_PROMPT, FakeToolLoopChat([]))
    state: dict[str, Any] = dict(plan.initial)
    assert (state["max_turns"], state["max_cost_usd"]) == (POOL_TURNS, pytest.approx(POOL_USD))

    after = _accumulate(state, await plan.context.nodes["survey"](state))

    assert remaining_turns(after) == POOL_TURNS
    assert remaining_cost_usd(after) == pytest.approx(POOL_USD)
    # 재개가 이 시도의 턴을 이어받도록 총 지출에는 그대로 오른다.
    assert after["model_turns_used"] == 1
    assert after["model_cost_usd"] > 0.0


async def test_예약_리스로_돈_선별자도_팬아웃_풀을_깎지_않는다() -> None:
    plan = _composed(TASK_CLEANUP_JOB, _cleanup_request(), TASK_CLEANUP_PROMPT, FakeToolLoopChat([]))
    state: dict[str, Any] = dict(plan.initial)

    after = _accumulate(state, await plan.context.nodes["triage"](state))

    assert remaining_turns(after) == POOL_TURNS
    assert remaining_cost_usd(after) == pytest.approx(POOL_USD)
    assert after["model_turns_used"] == 1


async def test_세_슬라이스의_노드가_같은_지출_채널을_적는다() -> None:
    recipe = _composed(RECIPE_SCAN_JOB, _recipe_request(), RECIPE_SCAN_PROMPT, FakeToolLoopChat([]))
    cleanup = _composed(TASK_CLEANUP_JOB, _cleanup_request(), TASK_CLEANUP_PROMPT, FakeToolLoopChat([]))
    title = _composed(
        TITLE_SUGGESTION_JOB,
        _title_request(),
        TITLE_SUGGESTION_PROMPT,
        FakeToolLoopChat([{"suggestions": [{"title": "제목", "rationale": "근거"}]}]),
    )

    updates = [
        await recipe.context.nodes["survey"](dict(recipe.initial)),
        await cleanup.context.nodes["triage"](dict(cleanup.initial)),
        await title.context.nodes["investigate"](dict(title.initial)),
    ]

    # 세 슬라이스가 같은 채널을 적어야 재개와 팬아웃이 어느 잡에서나 같은 값을 읽는다.
    for update in updates:
        assert set(_SPEND_KEYS) <= set(update)


async def test_바닥_예약은_실행_하나에_한_번만_주어진다() -> None:
    # 보고서의 재현 순서를 그대로 밟는다: survey 3 + probe 4 + 종합 5 + probe 2 + 종합 + repair 2.
    budget = ExecutionBudget(BUDGET_USD, mk_rates(), max_turns=MAX_TURNS)
    policy = load_reservation_policy()
    budget.reserve(policy.repair.turns, policy.repair.budget_share)
    budget.reserve(policy.survey.turns, policy.survey.budget_share)
    floor = budget.reserve(policy.synthesis_floor.turns, policy.synthesis_floor.budget_share)
    state: dict[str, Any] = dict(
        initial_recipe_scan_state(
            _recipe_request(),
            max_cost_usd=budget.remaining_budget_usd,
            max_turns=budget.remaining_turns,
        )
    )

    state = _accumulate(state, reserved_spend(cost_usd=0.16, turns_used=3))
    assert remaining_turns(state) == POOL_TURNS
    state = _accumulate(state, pool_spend(cost_usd=0.5, turns_used=4))
    assert remaining_turns(state) == 4

    first_floor = floor_lease(state, floor)
    first_lease = combine_leases([first_floor, _pool_lease(state)])
    assert first_lease.max_turns == 7
    state = _accumulate(state, floor_then_pool_spend(first_floor, cost_usd=0.4, turns_used=5))
    assert remaining_turns(state) == 2

    state = _accumulate(state, pool_spend(cost_usd=0.2, turns_used=2))
    second_floor = floor_lease(state, floor)
    second_lease = combine_leases([second_floor, _pool_lease(state)])
    # 두 번째 종합이 바닥 예약을 또 받으면 이 실행은 16턴 상한을 3턴 넘겨 19턴을 쓴다.
    assert second_floor.max_turns == 0
    assert second_lease.max_turns == 0

    state = _accumulate(state, reserved_spend(cost_usd=0.3, turns_used=2))
    assert state["model_turns_used"] == MAX_TURNS


def test_바닥_예약의_달러_몫이_0이_아니어도_상한을_넘지_않는다() -> None:
    # 계약이 synthesisFloor.budgetShare 를 0에서 올리는 순간 달러도 같은 초과를 겪는다.
    floor = AgentBudgetLease(max_turns=3, max_cost_usd=0.30)
    state: dict[str, Any] = {"max_turns": POOL_TURNS, "max_cost_usd": POOL_USD}

    first = floor_lease(state, floor)
    state = _accumulate(state, floor_then_pool_spend(first, cost_usd=0.30, turns_used=3))
    second = floor_lease(state, floor)

    assert first.max_cost_usd == pytest.approx(0.30)
    assert second.max_cost_usd == pytest.approx(0.0)
    assert state["floor_cost_usd"] == pytest.approx(floor.max_cost_usd)
    assert state["pool_cost_usd"] == pytest.approx(0.0)


def test_바닥_예약보다_적게_쓴_종합은_남은_예약만_다음에_넘긴다() -> None:
    floor = AgentBudgetLease(max_turns=3, max_cost_usd=0.0)
    state: dict[str, Any] = {"max_turns": POOL_TURNS, "max_cost_usd": POOL_USD}

    state = _accumulate(state, floor_then_pool_spend(floor_lease(state, floor), cost_usd=0.1, turns_used=1))

    assert floor_lease(state, floor).max_turns == 2
    # 바닥 예약으로 그은 턴은 풀에서 빠지지 않으므로 전문가의 몫이 줄지 않는다.
    assert remaining_turns(state) == POOL_TURNS


async def test_재개가_이어받은_턴만큼_예약을_다시_떼지_않는다() -> None:
    # 앞선 시도가 총량을 다 쓴 채 끊기면 두 번째 시도는 예약도 팬아웃도 열지 못한다.
    spent = PriorSpend(resumed=True, cost_usd=BUDGET_USD, turns=MAX_TURNS)
    plan = RECIPE_SCAN_JOB.compose(
        _recipe_request(),
        FakeTracerApi(),
        ExecutionTrace(),
        RECIPE_SCAN_PROMPT,
        ChatPair(FakeToolLoopChat([]), None),
        spent,
    )

    assert plan.initial["max_turns"] == 0
    assert plan.initial["max_cost_usd"] == pytest.approx(0.0)


def _pool_lease(state: dict[str, Any]) -> AgentBudgetLease:
    return AgentBudgetLease(max_turns=remaining_turns(state), max_cost_usd=remaining_cost_usd(state))


def test_예약을_뗀_잔량과_예약의_합이_실행_총량이다() -> None:
    budget = ExecutionBudget(BUDGET_USD, ModelRates(WIRE_MODEL_RATES), max_turns=MAX_TURNS)
    policy = load_reservation_policy()

    reserved = [
        budget.reserve(policy.repair.turns, policy.repair.budget_share).max_turns,
        budget.reserve(policy.survey.turns, policy.survey.budget_share).max_turns,
        budget.reserve(policy.synthesis_floor.turns, policy.synthesis_floor.budget_share).max_turns,
    ]

    assert sum(reserved) + budget.remaining_turns == MAX_TURNS
    assert budget.remaining_turns == POOL_TURNS
    assert budget.remaining_budget_usd == pytest.approx(POOL_USD)
