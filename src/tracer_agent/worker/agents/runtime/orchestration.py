"""오케스트레이터가 워커의 몫에 비례해 비용을 나누는 공통 규칙."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class SharedAssignment(Protocol):
    """워커에게 배분한 예산 비중을 노출하는 계약."""

    @property
    def share(self) -> int: ...


def allocate_cost_shares[Assignment: SharedAssignment](
    assignments: Sequence[Assignment], *, ceiling: float = 1.0
) -> list[tuple[Assignment, float]]:
    """각 워커의 몫에 예산 상한을 곱해 그 워커가 실행할 수 있는 비용과 함께 돌려준다."""
    total_share = sum(assignment.share for assignment in assignments)
    if total_share <= 0:
        raise ValueError("worker assignments must allocate at least one share")
    return [(assignment, ceiling * assignment.share / total_share) for assignment in assignments]
