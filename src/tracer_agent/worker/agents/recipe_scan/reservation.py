"""recipe-scan이 팬아웃 전에 repair·survey·synthesisFloor 순서로 뗄 예약 몫을 계약에서 읽는다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_CONTRACT_ROOT = Path(__file__).resolve().parents[5] / "contract"
_EXECUTION_BUDGET_PATH = _CONTRACT_ROOT / "agent" / "shared" / "execution.budget.json"
_RESERVATION_ORDER = ("repair", "survey", "synthesisFloor")


@dataclass(frozen=True)
class ReservationStep:
    """예약 하나가 떼는 턴 수와, 그 시점 잔량 대비 달러 비율이다."""

    turns: int
    budget_share: float


@dataclass(frozen=True)
class ReservationPolicy:
    """repair·survey·synthesisFloor 순서로 뗄 예약 셋이다."""

    repair: ReservationStep
    survey: ReservationStep
    synthesis_floor: ReservationStep


@lru_cache(maxsize=1)
def load_reservation_policy() -> ReservationPolicy:
    """계약의 뗄 순서를 검증하고 예약 셋을 읽는다."""
    document = json.loads(_EXECUTION_BUDGET_PATH.read_text(encoding="utf-8"))
    reservation = document["reservation"]
    order = tuple(reservation["order"])
    if order != _RESERVATION_ORDER:
        raise ValueError(
            f"execution.budget.json: reservation.order must be {_RESERVATION_ORDER}, got {order}"
        )
    steps = {
        name: ReservationStep(turns=reservation[name]["turns"], budget_share=reservation[name]["budgetShare"])
        for name in _RESERVATION_ORDER
    }
    return ReservationPolicy(
        repair=steps["repair"], survey=steps["survey"], synthesis_floor=steps["synthesisFloor"]
    )
