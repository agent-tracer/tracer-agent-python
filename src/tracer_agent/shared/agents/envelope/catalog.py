"""실행 봉투가 싣는 모델 단가와 실행 종류마다의 기본 모델과 한도를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

from ..shared.execution_limits import contract_execution_limits
from ..shared.job_kinds import AgentJobKind
from ..shared.model_rates import contract_model_rates
from ..shared.model_tiering import CHAT_KIND
from ..shared.models import ExecutionLimitsDTO, ModelRateDTO

# 실행 기계가 모든 캐시 경계에 요청하는 수명이며 청구하는 배수는 계약이 이 수명에 적은 값이다.
CACHE_WRITE_TTL = "1h"


def _rate(input_rate: float, output_rate: float) -> ModelRateDTO:
    """백만 토큰당 USD이며 캐시 단가는 실행이 실제로 요청하는 수명의 배수에서 나온다."""
    declared = contract_model_rates()
    return ModelRateDTO(
        input=input_rate,
        output=output_rate,
        cacheWrite=input_rate * declared.write_multiplier_for(CACHE_WRITE_TTL),
        cacheRead=input_rate * declared.read_multiplier,
    )


MODEL_RATES: dict[str, ModelRateDTO] = {
    name: _rate(*rates) for name, rates in contract_model_rates().base.items()
}


@dataclass(frozen=True)
class ExecutionCatalog:
    """실행 종류 하나가 쓸 기본 모델과 대체할 모델과 한도와 벽시계 상한이다."""

    default_model: str
    fallback_model: str | None
    limits: ExecutionLimitsDTO
    deadline_ms: int

    def wire_limits(self) -> dict[str, float]:
        """실행기가 지킬 예산과 턴과 출력 상한을 봉투가 실어 보낼 모양으로 낸다."""
        return self.limits.model_dump()


def _from_contract(agent_name: str) -> ExecutionCatalog:
    """계약이 그 종류에 적은 상한을 실행 카탈로그 한 칸으로 옮긴다."""
    declared = contract_execution_limits()[agent_name]
    return ExecutionCatalog(
        default_model=declared.default_model,
        fallback_model=declared.fallback_model,
        limits=ExecutionLimitsDTO(
            budgetUsd=declared.budget_usd,
            maxTurns=declared.max_turns,
            maxOutputTokens=declared.max_output_tokens,
        ),
        deadline_ms=declared.deadline_ms,
    )


# 봉투는 wire 값으로 종류를 가리키고 계약은 그 잡을 맡은 에이전트 이름으로 상한을 적는다.
CATALOG: dict[str, ExecutionCatalog] = {
    CHAT_KIND: _from_contract(CHAT_KIND),
    **{kind.wire: _from_contract(kind.value) for kind in AgentJobKind},
}

JOB_KINDS: frozenset[str] = frozenset(CATALOG) - {CHAT_KIND}


def wire_model_rates() -> dict[str, dict[str, float]]:
    """실행기가 자기 단가표를 갖지 않으므로 봉투에 실어 보낼 단가만 낸다."""
    return {name: rate.model_dump() for name, rate in MODEL_RATES.items()}
