"""recipe-scan이 실행 하나를 나눠 쓰는 규칙을 계약에서 읽는다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONTRACT_ROOT = Path(__file__).resolve().parents[5] / "contract"
_EXECUTION_BUDGET_PATH = _CONTRACT_ROOT / "agent" / "shared" / "execution.budget.json"
_TOOL_PATH = _CONTRACT_ROOT / "agent" / "recipe-scan" / "tool.json"
_RESERVATION_ORDER = ("repair", "survey", "synthesisFloor")


@lru_cache(maxsize=1)
def load_citable_id_list_limit() -> int:
    """조율자 요청이 한 줄에 적는 식별자 수의 상한을 계약에서 읽는다."""
    declared: Any = json.loads(_TOOL_PATH.read_text(encoding="utf-8"))
    return int(declared["limits"]["citableIdListLimit"])


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


@dataclass(frozen=True)
class WallClockPolicy:
    """단계 하나가 진전 없이 머물 수 있는 상한이며 실행 데드라인에 곱하는 비율이다."""

    survey: float
    probe: float
    synthesis: float
    probe_min_fraction: float


@dataclass(frozen=True)
class PricingPolicy:
    """예산 집행이 어느 모델의 단가를 쓰는지와 그 단가를 모를 때의 결말이다."""

    model: str
    on_unpriced_model: str


@lru_cache(maxsize=1)
def load_wall_clock_policy() -> WallClockPolicy:
    """단계마다의 벽시계 비율을 계약에서 읽는다."""
    declared = _budget_contract()["wallClock"]
    return WallClockPolicy(
        survey=float(declared["survey"]),
        probe=float(declared["probe"]),
        synthesis=float(declared["synthesis"]),
        probe_min_fraction=float(declared["probeMinFraction"]["value"]),
    )


@lru_cache(maxsize=1)
def load_pricing_policy() -> PricingPolicy:
    """값을 세는 기준 모델과 셀 수 없을 때의 결말을 계약에서 읽는다."""
    declared = _budget_contract()["pricing"]
    return PricingPolicy(
        model=str(declared["model"]["value"]),
        on_unpriced_model=str(declared["onUnpricedModel"]["value"]),
    )


@lru_cache(maxsize=1)
def _budget_contract() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(_EXECUTION_BUDGET_PATH.read_text(encoding="utf-8"))
    return document


@lru_cache(maxsize=1)
def load_reservation_policy() -> ReservationPolicy:
    """계약의 뗄 순서를 검증하고 예약 셋을 읽는다."""
    reservation = _budget_contract()["reservation"]
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
