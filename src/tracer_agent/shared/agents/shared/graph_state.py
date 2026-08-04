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
