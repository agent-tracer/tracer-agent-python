"""실행 봉투가 싣는 모델 단가와 실행 종류마다의 기본 모델과 한도를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

from ..shared.models import ExecutionLimitsDTO, ModelRateDTO

CHAT_KIND = "chat"

# 백만 토큰당 USD이며 캐시 쓰기는 입력의 1.25배, 캐시 읽기는 입력의 0.1배다.
MODEL_RATES: dict[str, ModelRateDTO] = {
    "claude-opus-5": ModelRateDTO(input=5.0, output=25.0, cacheWrite=6.25, cacheRead=0.5),
    "claude-sonnet-5": ModelRateDTO(input=3.0, output=15.0, cacheWrite=3.75, cacheRead=0.3),
    "claude-sonnet-4-6": ModelRateDTO(input=3.0, output=15.0, cacheWrite=3.75, cacheRead=0.3),
    "claude-haiku-4-5": ModelRateDTO(input=1.0, output=5.0, cacheWrite=1.25, cacheRead=0.1),
}


@dataclass(frozen=True)
class ExecutionCatalog:
    """실행 종류 하나가 쓸 기본 모델과 대체할 모델과 한도와 벽시계 상한이다."""

    default_model: str
    fallback_model: str | None
    # 종류마다 허용 모델을 좁게 두는 것이 절감의 첫 자리이며, 예산은 이 목록을 전제로 잡힌다.
    allowed_models: tuple[str, ...]
    limits: ExecutionLimitsDTO
    deadline_ms: int


CATALOG: dict[str, ExecutionCatalog] = {
    CHAT_KIND: ExecutionCatalog(
        default_model="claude-sonnet-4-6",
        fallback_model=None,
        allowed_models=("claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"),
        limits=ExecutionLimitsDTO(budgetUsd=1.2, maxTurns=14, maxOutputTokens=4_000),
        deadline_ms=600_000,
    ),
    "title.suggestion": ExecutionCatalog(
        default_model="claude-haiku-4-5",
        fallback_model="claude-haiku-4-5",
        allowed_models=("claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-5"),
        limits=ExecutionLimitsDTO(budgetUsd=0.2, maxTurns=12, maxOutputTokens=4_000),
        deadline_ms=300_000,
    ),
    "recipe.scan": ExecutionCatalog(
        default_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5",
        allowed_models=("claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"),
        limits=ExecutionLimitsDTO(budgetUsd=2.0, maxTurns=15, maxOutputTokens=16_000),
        deadline_ms=720_000,
    ),
    "task.cleanup": ExecutionCatalog(
        default_model="claude-haiku-4-5",
        fallback_model="claude-haiku-4-5",
        allowed_models=("claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-5"),
        limits=ExecutionLimitsDTO(budgetUsd=0.5, maxTurns=16, maxOutputTokens=16_000),
        deadline_ms=600_000,
    ),
}

JOB_KINDS: frozenset[str] = frozenset(CATALOG) - {CHAT_KIND}


def wire_model_rates() -> dict[str, dict[str, float]]:
    """실행기가 자기 단가표를 갖지 않으므로 봉투에 실어 보낼 단가만 낸다."""
    return {name: rate.model_dump() for name, rate in MODEL_RATES.items()}


def wire_limits(catalog: ExecutionCatalog) -> dict[str, float]:
    """실행기가 지킬 예산과 턴과 출력 상한을 봉투가 실어 보낼 모양으로 낸다."""
    return catalog.limits.model_dump()
