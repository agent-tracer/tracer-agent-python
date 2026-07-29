"""조율자가 워커에게 비용을 weight 비율로 나누는 공통 규칙을 검증한다."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tracer_agent.worker.agents.runtime.orchestration import allocate_cost_shares


@dataclass
class _Assignment:
    weight: int


def test_비용_몫은_weight_비율대로_나뉜다() -> None:
    assignments = [_Assignment(3), _Assignment(1)]

    shares = allocate_cost_shares(assignments)

    assert [share for _assignment, share in shares] == [0.75, 0.25]


def test_weight_배분이_없으면_거부한다() -> None:
    with pytest.raises(ValueError, match="at least one weight"):
        allocate_cost_shares([_Assignment(0), _Assignment(0)])
