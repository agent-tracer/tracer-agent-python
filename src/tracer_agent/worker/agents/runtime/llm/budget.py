"""에이전트 실행 한 번이 태우는 모델 비용을 누적하고 상한에서 끊는다."""

from __future__ import annotations

from typing import Protocol

from langchain_core.messages import AIMessage

from ..errors import BudgetExceeded
from ..pricing import ModelRates
from .trajectory import extract_token_usage, message_identity


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
    """병렬 노드까지 한 실행의 달러 상한을 함께 쓰는 장부다."""

    def __init__(self, max_cost_usd: float, rates: ModelRates) -> None:
        self._max = max_cost_usd
        self._rates = rates
        self._spent = 0.0
        self._peak = 0.0

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def max_cost_usd(self) -> float:
        return self._max

    @property
    def peak_call_cost_usd(self) -> float:
        return self._peak

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
