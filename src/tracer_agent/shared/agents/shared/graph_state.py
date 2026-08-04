"""에이전트 그래프 상태가 함께 싣는 예산 스냅숏 채널."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class BudgetSnapshotState(TypedDict):
    """재개가 앞선 시도의 지출을 읽는 자리이며 모든 에이전트 상태가 이 채널을 함께 상속한다."""

    # 팬아웃이 병렬로 갱신하고 재개가 합계를 읽으므로 마지막 쓰기가 아니라 누적으로 합친다.
    model_cost_usd: Annotated[float, operator.add]
    model_turns_used: Annotated[int, operator.add]


def fresh_budget_snapshot() -> BudgetSnapshotState:
    """실행을 처음 시작하는 상태의 예산 스냅숏이다."""
    return {"model_cost_usd": 0.0, "model_turns_used": 0}


class CostCeilingState(BudgetSnapshotState):
    """팬아웃이 남은 몫을 나눌 때 봐야 하는 이 실행의 달러 상한을 함께 싣는다."""

    max_cost_usd: float


class TurnCeilingState(CostCeilingState):
    """달러와 함께 턴도 나눠 쓰는 실행이 그 상한을 함께 싣는다."""

    max_turns: int


def remaining_cost_usd(state: CostCeilingState) -> float:
    """상한에서 이미 쓴 달러를 뺀 잔량이며 넘겨 쓴 실행은 0으로 본다."""
    return max(state["max_cost_usd"] - state.get("model_cost_usd", 0.0), 0.0)


def remaining_turns(state: TurnCeilingState) -> int:
    """상한에서 이미 그은 턴을 뺀 잔량이며 넘겨 쓴 실행은 0으로 본다."""
    return max(state["max_turns"] - state.get("model_turns_used", 0), 0)
