"""팬아웃 전에 떼어 두는 몫을 계약에서 읽어 잡 에이전트가 함께 쓴다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

_EXECUTION_BUDGET_PATH = CONTRACT_ROOT / "agent" / "shared" / "execution.budget.json"
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
def execution_budget_contract() -> dict[str, Any]:
    """실행 하나를 나눠 쓰는 규칙을 담은 계약 판이다."""
    document: dict[str, Any] = json.loads(_EXECUTION_BUDGET_PATH.read_text(encoding="utf-8"))
    return document


@lru_cache(maxsize=1)
def repair_attempts() -> int:
    """검증에 걸린 산출을 다시 받는 횟수이며 계약이 갖는다."""
    return int(execution_budget_contract()["reservation"]["repair"]["attempts"])


@lru_cache(maxsize=1)
def load_reservation_policy() -> ReservationPolicy:
    """계약의 뗄 순서를 검증하고 예약 셋을 읽는다."""
    reservation = execution_budget_contract()["reservation"]
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
