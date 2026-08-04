"""팬아웃이 보는 잔량 계산이 한 규칙으로 모여 있는지 검증한다."""

from __future__ import annotations

from typing import Any

from tracer_agent.shared.agents.shared.graph_state import remaining_cost_usd, remaining_turns


def _state(**values: Any) -> Any:
    return values


def test_상한에서_이미_쓴_몫을_뺀_잔량을_낸다() -> None:
    state = _state(max_cost_usd=2.0, max_turns=8, model_cost_usd=0.5, model_turns_used=3)

    assert remaining_cost_usd(state) == 1.5
    assert remaining_turns(state) == 5


def test_아직_쓴_것이_없는_상태는_상한을_그대로_낸다() -> None:
    # 재개가 아닌 첫 시도는 스냅숏 두 칸이 비어 있어도 상한 전부를 나눠 쓸 수 있어야 한다.
    state = _state(max_cost_usd=2.0, max_turns=8)

    assert remaining_cost_usd(state) == 2.0
    assert remaining_turns(state) == 8


def test_넘겨_쓴_실행의_잔량은_음수가_아니라_영이다() -> None:
    # 잔량을 몫으로 나누는 자리가 음수를 받으면 배분이 뒤집히므로 0에서 멈춘다.
    state = _state(max_cost_usd=1.0, max_turns=2, model_cost_usd=1.5, model_turns_used=5)

    assert remaining_cost_usd(state) == 0.0
    assert remaining_turns(state) == 0
