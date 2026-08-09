"""실행 종류마다의 기본 모델과 예산과 턴과 출력과 벽시계 상한을 계약에서 읽는다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .contract_root import CONTRACT_ROOT

EXECUTION_LIMITS_PATH = CONTRACT_ROOT / "agent" / "shared" / "execution.limits.json"


@dataclass(frozen=True)
class ContractExecutionLimits:
    """계약이 실행 종류 하나에 적은 상한이며 구현체가 값을 지어내지 않는다."""

    default_model: str
    fallback_model: str | None
    allowed_models: tuple[str, ...]
    budget_usd: float
    max_turns: int
    max_output_tokens: int
    deadline_ms: int


@lru_cache(maxsize=1)
def contract_execution_limits() -> dict[str, ContractExecutionLimits]:
    """계약이 적은 실행 종류를 이름으로 찾을 수 있게 낸다."""
    declared = json.loads(EXECUTION_LIMITS_PATH.read_text(encoding="utf-8"))["kinds"]
    return {
        name: ContractExecutionLimits(
            default_model=str(limits["defaultModel"]),
            fallback_model=_optional_model(limits.get("fallbackModel")),
            allowed_models=tuple(str(model) for model in limits["allowedModels"]),
            budget_usd=float(limits["budgetUsd"]),
            max_turns=int(limits["maxTurns"]),
            max_output_tokens=int(limits["maxOutputTokens"]),
            deadline_ms=int(limits["deadlineMs"]),
        )
        for name, limits in declared.items()
    }


def _optional_model(declared: object) -> str | None:
    return None if declared is None else str(declared)
