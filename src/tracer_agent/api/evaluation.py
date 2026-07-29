"""불변 prompt로 합성 평가를 동기 실행하는 내부 agent-host 계약이다."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..shared.agents.shared.models import ModelRateDTO
from ..shared.agents.shared.prompt_integrity import ResolvedFragmentsIntegrityDTO


class EvaluationPrompt(BaseModel):
    """실행 봉투는 파일이 정본인 프롬프트의 식별자와 해시만 싣는다."""

    model_config = ConfigDict(extra="forbid")
    versionId: str
    contentHash: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executionId: str
    attemptId: str
    backend: Literal["python"]
    model: str
    prompt: EvaluationPrompt
    toolContractVersion: str
    input: dict[str, Any]
    referenceOutput: dict[str, Any] | None = None
    maxCostUsd: float = Field(gt=0)
    apiKey: SecretStr
    modelRates: dict[str, ModelRateDTO] = Field(min_length=1)
    promptIntegrity: ResolvedFragmentsIntegrityDTO | None = None


async def run_evaluation(_req: EvaluationRunRequest) -> dict[str, object]:
    """봉투는 검증하되 실행할 수 없는 계약임을 재시도 없는 상태로 알린다."""
    # 봉투가 본문을 싣지 않는데 이 계약에는 어느 에이전트의 파일 프롬프트를 고를지 알 이름이 없다.
    raise HTTPException(
        status_code=422,
        detail="evaluation runner requires an agent-scoped prompt contract",
    )
