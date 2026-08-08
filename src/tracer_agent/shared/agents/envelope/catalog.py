"""실행 봉투가 싣는 모델 단가와 실행 종류마다의 기본 모델과 한도를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

from ..shared.job_kinds import AgentJobKind
from ..shared.model_tiering import CHAT_KIND
from ..shared.models import ExecutionLimitsDTO, ModelRateDTO

# 실행 기계가 모든 캐시 경계에 쓰는 수명이며 단가는 이 값이 정하는 배수를 따른다.
CACHE_WRITE_TTL = "1h"
# 캐시 쓰기 단가는 수명마다 입력 단가의 이 배수이며 값의 출처는 Anthropic 공식 문서다.
# https://platform.claude.com/docs/en/build-with-claude/prompt-caching
CACHE_WRITE_MULTIPLIER: dict[str, float] = {"5m": 1.25, "1h": 2.0}
CACHE_READ_MULTIPLIER = 0.1

_INPUT_OUTPUT_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _rate(input_rate: float, output_rate: float) -> ModelRateDTO:
    """백만 토큰당 USD이며 캐시 단가는 실행이 실제로 쓰는 수명의 배수에서 나온다."""
    return ModelRateDTO(
        input=input_rate,
        output=output_rate,
        cacheWrite=input_rate * CACHE_WRITE_MULTIPLIER[CACHE_WRITE_TTL],
        cacheRead=input_rate * CACHE_READ_MULTIPLIER,
    )


MODEL_RATES: dict[str, ModelRateDTO] = {name: _rate(*rates) for name, rates in _INPUT_OUTPUT_RATES.items()}


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


CATALOG: dict[str, ExecutionCatalog] = {
    CHAT_KIND: ExecutionCatalog(
        default_model="claude-sonnet-4-6",
        fallback_model=None,
        limits=ExecutionLimitsDTO(budgetUsd=1.2, maxTurns=14, maxOutputTokens=4_000),
        deadline_ms=600_000,
    ),
    AgentJobKind.TITLE_SUGGESTION.wire: ExecutionCatalog(
        default_model="claude-haiku-4-5",
        fallback_model="claude-haiku-4-5",
        limits=ExecutionLimitsDTO(budgetUsd=0.2, maxTurns=12, maxOutputTokens=4_000),
        deadline_ms=300_000,
    ),
    AgentJobKind.RECIPE_SCAN.wire: ExecutionCatalog(
        default_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5",
        limits=ExecutionLimitsDTO(budgetUsd=2.0, maxTurns=15, maxOutputTokens=16_000),
        deadline_ms=720_000,
    ),
    AgentJobKind.TASK_CLEANUP.wire: ExecutionCatalog(
        default_model="claude-haiku-4-5",
        fallback_model="claude-haiku-4-5",
        limits=ExecutionLimitsDTO(budgetUsd=0.5, maxTurns=16, maxOutputTokens=16_000),
        deadline_ms=600_000,
    ),
}

JOB_KINDS: frozenset[str] = frozenset(CATALOG) - {CHAT_KIND}


def wire_model_rates() -> dict[str, dict[str, float]]:
    """실행기가 자기 단가표를 갖지 않으므로 봉투에 실어 보낼 단가만 낸다."""
    return {name: rate.model_dump() for name, rate in MODEL_RATES.items()}
