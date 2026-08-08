"""에이전트 실행 한 번이 쓰는 모델 비용과 턴을 누적하고 상한에서 끊는다."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import AIMessage

from tracer_agent.shared.agents.shared.graph_state import SpendChannels, TurnCeilingState

from ..errors import BudgetExceeded
from ..pricing import ModelRates
from .pacing import landing_reserve_calls, provider_budget_backstop
from .trajectory import extract_token_usage, message_identity


@dataclass(frozen=True)
class AgentBudgetLease:
    """한 번의 호출에 떼어 준 턴과 달러 몫이며 그대로 노드 실행 상한으로 넘긴다."""

    max_turns: int
    max_cost_usd: float


def combine_leases(leases: Sequence[AgentBudgetLease]) -> AgentBudgetLease:
    """리스 여럿을 하나로 더해 턴과 달러 몫을 합친다."""
    return AgentBudgetLease(
        max_turns=sum(lease.max_turns for lease in leases),
        max_cost_usd=sum(lease.max_cost_usd for lease in leases),
    )


def reserved_spend(*, cost_usd: float, turns_used: int) -> SpendChannels:
    """예약 리스로 돈 호출의 정산이며 예약분은 뗄 때 이미 빠졌으므로 팬아웃 풀에서 다시 빼지 않는다."""
    return {
        "model_cost_usd": cost_usd,
        "model_turns_used": turns_used,
        "pool_cost_usd": 0.0,
        "pool_turns_used": 0,
        "floor_cost_usd": 0.0,
        "floor_turns_used": 0,
    }


def pool_spend(*, cost_usd: float, turns_used: int) -> SpendChannels:
    """팬아웃 몫으로 돈 호출의 정산이며 풀의 잔량이 그만큼 줄어든다."""
    return {
        "model_cost_usd": cost_usd,
        "model_turns_used": turns_used,
        "pool_cost_usd": cost_usd,
        "pool_turns_used": turns_used,
        "floor_cost_usd": 0.0,
        "floor_turns_used": 0,
    }


def floor_lease(state: TurnCeilingState, reserved: AgentBudgetLease) -> AgentBudgetLease:
    """실행당 한 번인 바닥 예약 가운데 아직 쓰지 않은 몫이다."""
    return AgentBudgetLease(
        max_turns=max(reserved.max_turns - state.get("floor_turns_used", 0), 0),
        max_cost_usd=max(reserved.max_cost_usd - state.get("floor_cost_usd", 0.0), 0.0),
    )


def floor_then_pool_spend(floor: AgentBudgetLease, *, cost_usd: float, turns_used: int) -> SpendChannels:
    """바닥 예약과 풀을 합쳐 돈 호출의 정산이며 예약분을 먼저 채우고 남는 만큼만 풀에서 뺀다."""
    floor_turns = min(turns_used, floor.max_turns)
    floor_usd = min(cost_usd, floor.max_cost_usd)
    return {
        "model_cost_usd": cost_usd,
        "model_turns_used": turns_used,
        "pool_cost_usd": cost_usd - floor_usd,
        "pool_turns_used": turns_used - floor_turns,
        "floor_cost_usd": floor_usd,
        "floor_turns_used": floor_turns,
    }


def lease_shares(
    requested_turns: Sequence[int], available_turns: int, available_usd: float
) -> list[AgentBudgetLease]:
    """몫의 나머지 배분 규칙대로 요청 턴을 가용 턴과 달러로 나눈다."""
    if not requested_turns:
        return []
    granted_turns = _clamp_turns_without_leak(requested_turns, max(available_turns, 0))
    turns_sum = sum(granted_turns)
    return [
        AgentBudgetLease(
            max_turns=turns,
            max_cost_usd=0.0 if turns_sum == 0 else available_usd * turns / turns_sum,
        )
        for turns in granted_turns
    ]


def _clamp_turns_without_leak(requested: Sequence[int], available: int) -> list[int]:
    """가용 턴이 요청 합에 못 미칠 때 내림에서 남는 턴을 몫이 큰 순서로 하나씩 돌려준다."""
    total = sum(requested)
    if total <= available:
        return list(requested)

    count = len(requested)
    # 동률이면 인덱스가 작은 쪽이 먼저이도록 sorted의 안정성에 기대지 않고 보조 키로 명시한다.
    rank_descending = sorted(range(count), key=lambda index: (-requested[index], index))

    if count > available:
        granted = [0] * count
        for index in rank_descending[:available]:
            granted[index] = 1
        return granted

    spare = max(available - count, 0)
    over = total - count
    granted = [1 + (math.floor(((value - 1) * spare) / over) if over > 0 else 0) for value in requested]
    remainder = max(available - sum(granted), 0)
    for index in rank_descending[:remainder]:
        granted[index] += 1
    return granted


def _ceiling(max_cost_usd: float, peak_call_cost_usd: float, landed: bool) -> float:
    """종료는 도구만 닫으므로 마무리 호출 몫만큼 위까지 열되 그 밖으로는 넘기지 않는다."""
    if not landed:
        return max_cost_usd
    return max_cost_usd + peak_call_cost_usd * landing_reserve_calls()


def _execution_ceiling(max_cost_usd: float, peak_call_cost_usd: float, landed: bool) -> float:
    """실행 하나가 공급자에게 낼 수 있는 달러의 끝이며 마무리 몫으로 열린 여유도 이 안에 머문다."""
    return min(
        _ceiling(max_cost_usd, peak_call_cost_usd, landed),
        provider_budget_backstop(max_cost_usd),
    )


class ModelCallBudget(Protocol):
    """표준 에이전트 미들웨어가 요구하는 호출 비용 장부 계약이다."""

    @property
    def spent(self) -> float: ...

    @property
    def landing(self) -> bool: ...

    def land(self) -> None: ...

    def charge(self, message: AIMessage) -> None: ...


def single_loop_budget(
    agent_name: str,
    model_name: str,
    max_cost_usd: float,
    rates: ModelRates,
    spent: float = 0.0,
) -> SharedToolLoopBudget:
    """루프가 하나뿐인 실행의 장부이며 집행은 실행 장부 한 곳만 지나간다."""
    # 상태 채널이 증분을 누적하므로 이어받은 몫을 실행 장부의 시작 지출로 싣는다.
    execution = ExecutionBudget(max_cost_usd, rates, spent_usd=spent)
    return execution.new_loop(agent_name, model_name)


class ExecutionBudget:
    """병렬 노드까지 한 실행의 달러 상한을 함께 쓰는 장부이며 팬아웃 전에 뗄 턴 원장도 갖는다."""

    def __init__(
        self,
        max_cost_usd: float,
        rates: ModelRates,
        max_turns: int | None = None,
        *,
        spent_usd: float = 0.0,
        turns_used: int = 0,
    ) -> None:
        self._max = max_cost_usd
        self._rates = rates
        # 이어받은 실행은 앞선 시도의 지출을 이어받아 시작해야 상한이 실행 하나에 한 번만 열린다.
        self._spent = spent_usd
        self._peak = 0.0
        self._remaining_turns = None if max_turns is None else max(max_turns - turns_used, 0)
        self._remaining_budget_usd = max(max_cost_usd - spent_usd, 0.0)

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def max_cost_usd(self) -> float:
        return self._max

    @property
    def peak_call_cost_usd(self) -> float:
        return self._peak

    @property
    def remaining_turns(self) -> int:
        """턴 원장이 아직 아무에게도 떼어 주지 않은 턴이다."""
        return self._turn_ledger()

    @property
    def remaining_budget_usd(self) -> float:
        """턴 원장이 아직 아무에게도 떼어 주지 않은 달러다."""
        return self._remaining_budget_usd

    def reserve(self, turns: int, budget_share: float = 0.0) -> AgentBudgetLease:
        """뒤의 리스가 침범하지 못하도록 잔량에서 먼저 턴과 몫을 떼어 별도로 가진다."""
        if turns < 0:
            raise ValueError(f"turns must be >= 0, got {turns}")
        if not (0.0 <= budget_share <= 1.0):
            raise ValueError(f"budget_share must be in [0, 1], got {budget_share}")
        remaining_turns = self._turn_ledger()
        granted_turns = min(turns, remaining_turns)
        self._remaining_turns = remaining_turns - granted_turns
        granted_usd = self._remaining_budget_usd * budget_share
        self._remaining_budget_usd -= granted_usd
        return AgentBudgetLease(max_turns=granted_turns, max_cost_usd=granted_usd)

    def _turn_ledger(self) -> int:
        if self._remaining_turns is None:
            raise RuntimeError("execution budget has no turn ledger configured")
        return self._remaining_turns

    def price(self, agent_name: str, model_name: str, message: AIMessage) -> float:
        """실제로 답한 모델의 단가로 이 호출의 비용을 셈하며 단가를 모르면 상한을 지킬 수 없어 끊는다."""
        usage = extract_token_usage(message)
        actual_model, _request_id = message_identity(message)
        priced_model = actual_model or model_name
        cost = self._rates.estimate_cost_usd(priced_model, usage.to_dto()) if usage else None
        if cost is None:
            raise BudgetExceeded(f"{agent_name} cannot enforce its internal budget for model {priced_model}")
        return cost

    def charge(
        self,
        agent_name: str,
        cost: float,
        *,
        loop_spent: float,
        loop_max_cost_usd: float,
        loop_peak: float,
        loop_landed: bool,
    ) -> None:
        """이미 답한 호출의 비용을 실행 장부에 더하고 상한을 넘겼으면 그 뒤의 호출을 끊는다."""
        # 여유는 이 호출 전의 최고 호출로 잡아, 비싼 마무리 호출이 스스로 상한을 밀어 올리지 못하게 한다.
        execution_ceiling = _execution_ceiling(self._max, self._peak, loop_landed)
        # 아직 아무 호출도 하지 않은 루프는 자기 최고 호출을 모르므로 실행이 본 최고 호출로 여유를 잡는다.
        loop_ceiling = _ceiling(loop_max_cost_usd, max(loop_peak, self._peak), loop_landed)
        # 공급자가 이미 답한 호출이라 상한에서 끊더라도 그 비용은 장부에 남는다.
        self._spent += cost
        self._peak = max(self._peak, cost)
        if self._spent > execution_ceiling:
            raise BudgetExceeded(f"{agent_name} exceeded execution model budget ${self._max:.2f}")
        if loop_spent > loop_ceiling:
            raise BudgetExceeded(f"{agent_name} exceeded assigned model budget ${loop_max_cost_usd:.2f}")

    def new_loop(
        self, agent_name: str, model_name: str, *, max_cost_usd: float | None = None
    ) -> SharedToolLoopBudget:
        ceiling = self._max if max_cost_usd is None else min(max_cost_usd, self._max)
        return SharedToolLoopBudget(agent_name, model_name, self, ceiling)


class SharedToolLoopBudget:
    """실행 장부에 실제 호출 비용을 반영하고 이 노드가 쓴 증분도 따로 낸다."""

    def __init__(
        self, agent_name: str, model_name: str, execution: ExecutionBudget, max_cost_usd: float
    ) -> None:
        self._agent = agent_name
        self._model = model_name
        self._execution = execution
        self._max = max_cost_usd
        self._spent = 0.0
        self._peak = 0.0
        self._landed = False

    @property
    def agent_name(self) -> str:
        """이 도구 루프가 관측과 계측에 싣는 이름이다."""
        return self._agent

    @property
    def spent(self) -> float:
        return self._execution.spent

    @property
    def delta(self) -> float:
        return self._spent

    @property
    def landing(self) -> bool:
        reserve = landing_reserve_calls()
        return (
            self._spent + self._peak * reserve >= self._max
            or self._execution.spent + self._execution.peak_call_cost_usd * reserve
            >= self._execution.max_cost_usd
        )

    def land(self) -> None:
        self._landed = True

    def charge(self, message: AIMessage) -> None:
        cost = self._execution.price(self._agent, self._model, message)
        peak_before = self._peak
        self._spent += cost
        self._peak = max(self._peak, cost)
        self._execution.charge(
            self._agent,
            cost,
            loop_spent=self._spent,
            loop_max_cost_usd=self._max,
            loop_peak=peak_before,
            loop_landed=self._landed,
        )
