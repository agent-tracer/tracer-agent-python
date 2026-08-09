"""recipe-scan이 실행 하나를 나눠 쓰는 규칙을 계약에서 읽는다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ....shared.agents.shared.contract_root import CONTRACT_ROOT
from ..shared.execution_reservation import (
    ReservationPolicy,
    ReservationStep,
    WallClockPolicy,
    execution_budget_contract,
    load_reservation_policy,
    load_wall_clock_policy,
)

_TOOL_PATH = CONTRACT_ROOT / "agent" / "recipe-scan" / "tool.json"

__all__ = [
    "PricingPolicy",
    "ReservationPolicy",
    "ReservationStep",
    "WallClockPolicy",
    "load_citable_id_list_limit",
    "load_pricing_policy",
    "load_reservation_policy",
    "load_wall_clock_policy",
]


@lru_cache(maxsize=1)
def load_citable_id_list_limit() -> int:
    """조율자 요청이 한 줄에 적는 식별자 수의 상한을 계약에서 읽는다."""
    declared: Any = json.loads(_TOOL_PATH.read_text(encoding="utf-8"))
    return int(declared["limits"]["citableIdListLimit"])


@dataclass(frozen=True)
class PricingPolicy:
    """예산 집행이 어느 모델의 단가를 쓰는지와 그 단가를 모를 때의 결말이다."""

    model: str
    on_unpriced_model: str


@lru_cache(maxsize=1)
def load_pricing_policy() -> PricingPolicy:
    """값을 세는 기준 모델과 셀 수 없을 때의 결말을 계약에서 읽는다."""
    declared = execution_budget_contract()["pricing"]
    return PricingPolicy(
        model=str(declared["model"]["value"]),
        on_unpriced_model=str(declared["onUnpricedModel"]["value"]),
    )
