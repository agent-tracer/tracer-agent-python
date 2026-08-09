"""모델 단가와 캐시 배수의 정본을 계약에서 읽는다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .contract_root import CONTRACT_ROOT

MODEL_RATES_PATH = CONTRACT_ROOT / "agent" / "shared" / "model.rates.json"


@dataclass(frozen=True)
class ContractModelRates:
    """계약이 적은 백만 토큰당 단가와 캐시 배수이며 구현체가 값을 더하지 않는다."""

    base: dict[str, tuple[float, float]]
    write_multiplier: dict[str, float]
    read_multiplier: float
    default_ttl: str

    def write_multiplier_for(self, ttl: str) -> float:
        """실행기가 실제로 요청하는 수명의 쓰기 배수를 낸다."""
        return self.write_multiplier[ttl]


def cache_write_ttl() -> str:
    """실행 기계가 모든 캐시 경계에 요청하는 수명이다."""
    # 수명을 고를 수 있는 축이 기본 수명에 맞추어야 같은 실행이 두 축에서 같은 값으로 청구된다.
    return contract_model_rates().default_ttl


@lru_cache(maxsize=1)
def contract_model_rates() -> ContractModelRates:
    """계약이 소유한 단가표를 낸다."""
    declared = json.loads(MODEL_RATES_PATH.read_text(encoding="utf-8"))
    cache = declared["cache"]
    return ContractModelRates(
        base={name: (float(rate["input"]), float(rate["output"])) for name, rate in declared["base"].items()},
        write_multiplier={ttl: float(value) for ttl, value in cache["writeMultiplier"].items()},
        read_multiplier=float(cache["readMultiplier"]),
        default_ttl=str(cache["defaultTtl"]),
    )
