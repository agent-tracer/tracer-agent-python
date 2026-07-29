"""오케스트레이터가 워커 weight에 비례해 비용을 나누는 공통 규칙."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class WeightedAssignment(Protocol):
    """워커에게 배분한 예산 비중을 노출하는 계약."""

    weight: int


def allocate_cost_shares[Assignment: WeightedAssignment](
    assignments: Sequence[Assignment], *, ceiling: float = 1.0
) -> list[tuple[Assignment, float]]:
    """각 워커의 weight 몫에 예산 상한을 곱해 그 워커가 태울 수 있는 비용과 함께 돌려준다."""
    total_weight = sum(assignment.weight for assignment in assignments)
    if total_weight <= 0:
        raise ValueError("worker assignments must allocate at least one weight")
    return [(assignment, ceiling * assignment.weight / total_weight) for assignment in assignments]
