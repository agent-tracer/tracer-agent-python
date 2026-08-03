"""에이전트 실행 한 번이 태우는 모델 비용과 턴을 누적하고 상한에서 끊는다."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import count
from typing import Protocol

from langchain_core.messages import AIMessage

from ..errors import BudgetExceeded
from ..pricing import ModelRates
from .trajectory import extract_token_usage, message_identity

_lease_ids = count(1)


@dataclass(frozen=True)
class AgentBudgetLease:
    """한 번의 호출에 떼어 준 턴과 달러 몫이며 그대로 노드 실행 상한으로 넘긴다."""

    max_turns: int
    max_cost_usd: float
    # 실행 장부가 예약과 정산을 대조하는 열쇠이며 리스마다 하나뿐이다.
    lease_id: int = field(default_factory=lambda: next(_lease_ids), compare=False)


@dataclass(frozen=True)
class AgentBudgetSpend:
    """한 번의 호출이 실제로 쓴 턴과 비용이며 공급자가 보고하지 않으면 None이다."""

    cost_usd: float | None
    num_turns: int | None


@dataclass(frozen=True)
class _ReservedShare:
    turns: int
    usd: float


_EMPTY_RESERVED_SHARE = _ReservedShare(turns=0, usd=0.0)


def combine_leases(leases: Sequence[AgentBudgetLease]) -> AgentBudgetLease:
    """리스 여럿을 하나로 더해 턴과 달러 몫을 합친다."""
    return AgentBudgetLease(
        max_turns=sum(lease.max_turns for lease in leases),
        max_cost_usd=sum(lease.max_cost_usd for lease in leases),
    )


def lease_shares(
    requested_turns: Sequence[int], available_turns: int, available_usd: float
) -> list[AgentBudgetLease]:
    """weight 나머지 배분 규칙대로 요청 턴을 가용 턴과 달러로 나눈다."""
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
    """가용 턴이 요청 합에 못 미칠 때 내림에서 남는 턴을 weight가 큰 순서로 하나씩 돌려준다."""
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


class ModelCallBudget(Protocol):
    """표준 에이전트 미들웨어가 요구하는 호출 비용 장부 계약이다."""

    @property
    def spent(self) -> float: ...

    @property
    def landing(self) -> bool: ...

    def land(self) -> None: ...

    def charge(self, message: AIMessage) -> None: ...


class ToolLoopBudget:
    """루프 한 번의 모델 비용을 누적하고 상한에서 끊는다."""

    def __init__(
        self,
        agent_name: str,
        model_name: str,
        max_cost_usd: float,
        rates: ModelRates,
        spent: float = 0.0,
    ) -> None:
        self._agent = agent_name
        self._model = model_name
        self._max = max_cost_usd
        self._rates = rates
        self._peak = 0.0
        self._landed = False
        self._spent = spent

    @property
    def spent(self) -> float:
        """이 루프가 지금까지 태운 모델 비용이다."""
        return self._spent

    @property
    def landing(self) -> bool:
        """지금까지 가장 비쌌던 호출을 한 번 더 감당할 수 없는지 알린다."""
        return self._spent + self._peak >= self._max

    def land(self) -> None:
        """결론만 받는 마지막 호출로 넘어갔음을 알린다."""
        self._landed = True

    def charge(self, message: AIMessage) -> None:
        usage = extract_token_usage(message)
        # 폴백이 걸리면 응답이 primary와 다른 모델에서 왔으므로 실제 응답 모델로 단가를 고른다.
        actual_model, _request_id = message_identity(message)
        priced_model = actual_model or self._model
        cost = self._rates.estimate_cost_usd(priced_model, usage.to_dto()) if usage else None
        if cost is None:
            raise BudgetExceeded(f"{self._agent} cannot enforce its internal budget for model {priced_model}")
        self._spent += cost
        self._peak = max(self._peak, cost)
        # 착지한 뒤의 지출은 이미 끝난 실행의 마지막 호출이라 끊어봐야 산출물만 잃는다.
        if not self._landed and self._spent > self._max:
            raise BudgetExceeded(f"{self._agent} exceeded internal model budget ${self._max:.2f}")


class ExecutionBudget:
    """병렬 노드까지 한 실행의 달러 상한을 함께 쓰는 장부이며 팬아웃 전에 뗄 턴 원장도 갖는다."""

    def __init__(self, max_cost_usd: float, rates: ModelRates, max_turns: int | None = None) -> None:
        self._max = max_cost_usd
        self._rates = rates
        self._spent = 0.0
        self._peak = 0.0
        self._remaining_turns = max_turns
        self._remaining_budget_usd = max_cost_usd
        self._reservations: dict[int, _ReservedShare] = {}
        self._settled: set[int] = set()

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

    def has_remaining_capacity(self, required_turns: int = 1) -> bool:
        """요청한 턴과 비용 잔량을 모두 감당할 수 있는지 알린다."""
        return self._turn_ledger() >= required_turns and self._remaining_budget_usd > 0

    def reserve(self, turns: int, budget_share: float = 0.0) -> AgentBudgetLease:
        """뒤의 리스가 침범하지 못하도록 잔량에서 먼저 턴과 몫을 떼어 별도로 쥔다."""
        if turns < 0:
            raise ValueError(f"turns must be >= 0, got {turns}")
        if not (0.0 <= budget_share <= 1.0):
            raise ValueError(f"budget_share must be in [0, 1], got {budget_share}")
        remaining_turns = self._turn_ledger()
        granted_turns = min(turns, remaining_turns)
        self._remaining_turns = remaining_turns - granted_turns
        granted_usd = self._remaining_budget_usd * budget_share
        self._remaining_budget_usd -= granted_usd
        lease = AgentBudgetLease(max_turns=granted_turns, max_cost_usd=granted_usd)
        self._reservations[lease.lease_id] = _ReservedShare(turns=granted_turns, usd=granted_usd)
        return lease

    def lease(self, share: float) -> AgentBudgetLease:
        """잔량의 share 몫을 떼어 그 호출의 상한으로 준다."""
        if not (0.0 < share <= 1.0):
            raise ValueError(f"share must be in (0, 1], got {share}")
        return AgentBudgetLease(
            max_turns=math.floor(self._turn_ledger() * share),
            max_cost_usd=self._remaining_budget_usd * share,
        )

    def lease_many(self, requested_turns: Sequence[int], share: float) -> list[AgentBudgetLease]:
        """잔량의 share 몫을 계약의 나머지 배분 규칙대로 요청 턴에 나눈다."""
        if not (0.0 < share <= 1.0):
            raise ValueError(f"share must be in (0, 1], got {share}")
        if not requested_turns:
            return []
        available_turns = math.floor(self._turn_ledger() * share)
        available_usd = self._remaining_budget_usd * share
        return lease_shares(requested_turns, available_turns, available_usd)

    def settle(self, lease: AgentBudgetLease, spend: AgentBudgetSpend) -> None:
        """떼어 준 몫에 실제 지출을 대조해 잔량을 되돌리되 예약분을 두 번 빼지 않는다."""
        key = lease.lease_id
        if key in self._settled:
            raise RuntimeError("budget lease is already settled")
        self._settled.add(key)
        reserved = self._reservations.get(key, _EMPTY_RESERVED_SHARE)

        turns_used = spend.num_turns if spend.num_turns is not None else lease.max_turns
        remaining_turns = self._turn_ledger()
        self._remaining_turns = max(0, remaining_turns + reserved.turns - turns_used)

        usd_used = spend.cost_usd if spend.cost_usd is not None else lease.max_cost_usd
        self._remaining_budget_usd = max(0.0, self._remaining_budget_usd + reserved.usd - usd_used)

    def combine(self, leases: Sequence[AgentBudgetLease]) -> AgentBudgetLease:
        """예약한 바닥과 잔량의 몫을 하나로 묶되 예약분을 두 번 빼지 않는다."""
        combined = combine_leases(leases)
        reserved_turns = 0
        reserved_usd = 0.0
        for one in leases:
            reserved = self._reservations.get(one.lease_id, _EMPTY_RESERVED_SHARE)
            reserved_turns += reserved.turns
            reserved_usd += reserved.usd
        if reserved_turns > 0 or reserved_usd != 0:
            self._reservations[combined.lease_id] = _ReservedShare(turns=reserved_turns, usd=reserved_usd)
        return combined

    def _turn_ledger(self) -> int:
        if self._remaining_turns is None:
            raise RuntimeError("execution budget has no turn ledger configured")
        return self._remaining_turns

    def charge(
        self,
        agent_name: str,
        model_name: str,
        message: AIMessage,
        *,
        loop_spent: float,
        loop_max_cost_usd: float,
        loop_landed: bool,
    ) -> float:
        """실제 응답 모델 기준 비용을 원자적으로 실행 장부에 더한다."""
        usage = extract_token_usage(message)
        actual_model, _request_id = message_identity(message)
        priced_model = actual_model or model_name
        cost = self._rates.estimate_cost_usd(priced_model, usage.to_dto()) if usage else None
        if cost is None:
            raise BudgetExceeded(f"{agent_name} cannot enforce its internal budget for model {priced_model}")
        # 착지한 뒤의 지출은 이미 끝난 실행의 마지막 호출이라 끊어봐야 산출물만 잃는다.
        if not loop_landed:
            if self._spent + cost > self._max:
                raise BudgetExceeded(f"{agent_name} exceeded execution model budget ${self._max:.2f}")
            if loop_spent + cost > loop_max_cost_usd:
                raise BudgetExceeded(f"{agent_name} exceeded assigned model budget ${loop_max_cost_usd:.2f}")
        self._spent += cost
        self._peak = max(self._peak, cost)
        return cost

    def new_loop(
        self, agent_name: str, model_name: str, *, max_cost_usd: float | None = None
    ) -> SharedToolLoopBudget:
        ceiling = self._max if max_cost_usd is None else min(max_cost_usd, self._max)
        return SharedToolLoopBudget(agent_name, model_name, self, ceiling)


class SharedToolLoopBudget:
    """실행 장부에 실제 호출 비용을 반영하고, 이 노드가 쓴 증분도 따로 준다."""

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
        return (
            self._spent + self._peak >= self._max
            or self._execution.spent + self._execution.peak_call_cost_usd >= self._execution.max_cost_usd
        )

    def land(self) -> None:
        self._landed = True

    def charge(self, message: AIMessage) -> None:
        cost = self._execution.charge(
            self._agent,
            self._model,
            message,
            loop_spent=self._spent,
            loop_max_cost_usd=self._max,
            loop_landed=self._landed,
        )
        self._spent += cost
        self._peak = max(self._peak, cost)
