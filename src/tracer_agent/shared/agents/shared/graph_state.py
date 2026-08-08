"""에이전트 그래프 상태가 함께 싣는 예산 스냅숏 채널."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class BudgetSnapshotState(TypedDict):
    """재개가 앞선 시도의 총 지출을 읽는 자리이며 모든 에이전트 상태가 이 채널을 함께 상속한다."""

    # 팬아웃이 병렬로 갱신하고 재개가 합계를 읽으므로 마지막 쓰기가 아니라 누적으로 합친다.
    model_cost_usd: Annotated[float, operator.add]
    model_turns_used: Annotated[int, operator.add]


def fresh_budget_snapshot() -> BudgetSnapshotState:
    """실행을 처음 시작하는 상태의 예산 스냅숏이다."""
    return {"model_cost_usd": 0.0, "model_turns_used": 0}


class SpendChannels(BudgetSnapshotState):
    """호출 하나의 정산이 적고 상태가 누적으로 갖는 지출 채널이며 세 슬라이스가 같은 규약으로 쓴다."""

    # 예약을 뗀 뒤의 팬아웃 잔량에서만 빠지므로 예약 리스로 돈 호출의 지출은 담지 않는다.
    pool_cost_usd: Annotated[float, operator.add]
    pool_turns_used: Annotated[int, operator.add]
    # 실행당 한 번인 바닥 예약에서 이미 쓴 몫이며 노드가 두 번 돌아도 그 예약은 다시 주어지지 않는다.
    floor_cost_usd: Annotated[float, operator.add]
    floor_turns_used: Annotated[int, operator.add]


def fresh_spend_channels() -> SpendChannels:
    """실행을 처음 시작하는 상태의 지출 채널이다."""
    return {
        **fresh_budget_snapshot(),
        "pool_cost_usd": 0.0,
        "pool_turns_used": 0,
        "floor_cost_usd": 0.0,
        "floor_turns_used": 0,
    }


class CostCeilingState(SpendChannels):
    """팬아웃이 남은 몫을 나눌 때 봐야 하는 이 실행의 달러 상한을 함께 싣는다."""

    max_cost_usd: float


class TurnCeilingState(CostCeilingState):
    """달러와 함께 턴도 나눠 쓰는 실행이 그 상한을 함께 싣는다."""

    max_turns: int


def remaining_cost_usd(state: CostCeilingState) -> float:
    """팬아웃이 나눠 쓸 풀에서 이미 쓴 달러를 뺀 잔량이며 넘겨 쓴 실행은 0으로 본다."""
    return max(state["max_cost_usd"] - state.get("pool_cost_usd", 0.0), 0.0)


def remaining_turns(state: TurnCeilingState) -> int:
    """팬아웃이 나눠 쓸 풀에서 이미 그은 턴을 뺀 잔량이며 넘겨 쓴 실행은 0으로 본다."""
    return max(state["max_turns"] - state.get("pool_turns_used", 0), 0)
