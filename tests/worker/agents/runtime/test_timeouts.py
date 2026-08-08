"""데드라인 하나에서 유도하는 노드 벽시계 상한이 계약의 비율만 근거로 삼는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import shared_contract
from tracer_agent.worker.agents.runtime.timeouts import deadline_fraction_s, weighted_wall_clock_s


def _probe_min_fraction() -> float:
    return float(shared_contract("execution.budget.json")["wallClock"]["probeMinFraction"]["value"])


def test_데드라인의_비율만큼을_초로_낸다() -> None:
    assert deadline_fraction_s(600_000, 0.3) == pytest.approx(180.0)


def test_바닥_비율을_스스로_갖지_않는다() -> None:
    # 계약의 probeMinFraction 을 기본값으로 복제해 두면 계약이 바뀌어도 이 자리는 옛 값을 계속 쓴다.
    with pytest.raises(TypeError):
        weighted_wall_clock_s(100.0, 0.5, 1.0)  # type: ignore[call-arg]


def test_몫이_아주_작아도_바닥_비율만큼은_받는다() -> None:
    floor = _probe_min_fraction()

    granted = weighted_wall_clock_s(100.0, 0.001, 10.0, min_fraction=floor)

    assert granted == pytest.approx(100.0 * floor)


def test_몫이_클수록_상한을_많이_받고_전체를_넘지_않는다() -> None:
    floor = _probe_min_fraction()

    half = weighted_wall_clock_s(100.0, 5.0, 10.0, min_fraction=floor)
    whole = weighted_wall_clock_s(100.0, 20.0, 10.0, min_fraction=floor)

    assert half == pytest.approx(50.0)
    assert whole == pytest.approx(100.0)
