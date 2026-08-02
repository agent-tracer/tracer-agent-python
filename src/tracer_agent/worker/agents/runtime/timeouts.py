"""실행 데드라인 하나에서 그래프 노드가 쓸 벽시계 상한을 유도한다."""

from __future__ import annotations


def deadline_fraction_s(deadline_ms: int, fraction: float) -> float:
    """실행 데드라인의 fraction만큼을 초 단위 벽시계 상한으로 낸다."""
    return (deadline_ms / 1000) * fraction


def weighted_wall_clock_s(
    ceiling_s: float, cost_share: float, budget_usd: float, *, min_fraction: float = 0.3
) -> float:
    """워커가 받은 달러 몫에 비례해 벽시계 상한을 내어 큰 몫을 받은 워커를 먼저 끊지 않는다."""
    fraction = cost_share / budget_usd if budget_usd > 0 else 1.0
    return ceiling_s * max(min(fraction, 1.0), min_fraction)
