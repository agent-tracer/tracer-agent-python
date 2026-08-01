"""세 에이전트가 공유하는 HTTP 실행 봉투와 응답 계약."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _drop_docstring(schema: dict[str, Any]) -> None:
    schema.pop("description", None)


TrimmedStr = Annotated[str, BeforeValidator(_strip)]
Language = Literal["auto", "ko", "en", "ja", "zh"]


class ModelFacing(BaseModel):
    """모델이 스키마로 받는 계약이라 사람이 읽는 docstring을 JSON Schema 설명으로 내보내지 않는다."""

    model_config = ConfigDict(json_schema_extra=_drop_docstring)


class CompletionCallback(BaseModel):
    """실행 결과를 요청한 HTTP 연결이 아니라 이 창구로 돌려받는다."""

    url: TrimmedStr = Field(min_length=1)
    token: TrimmedStr = Field(min_length=1)


class ModelRateDTO(BaseModel):
    """백만 토큰당 USD 단가이며 캐시 쓰기·읽기는 입력과 별도 단가다."""

    input: float = Field(gt=0)
    output: float = Field(gt=0)
    cacheWrite: float = Field(gt=0)
    cacheRead: float = Field(gt=0)


class ExecutionLimitsDTO(BaseModel):
    """이 실행이 지킬 한도이며 서버 카탈로그가 기능마다 정한다."""

    budgetUsd: float = Field(gt=0)
    # 예산이 아니라 폭주만 끊는 느슨한 그물이며 모델의 self-pacing 표시에도 쓰인다.
    maxTurns: int = Field(gt=0)
    maxOutputTokens: int = Field(gt=0)


class AgentExecutionEnvelope(BaseModel):
    """한 실행이 쓸 모델과 자격과 단가와 한도이며, 이 서비스는 어느 값도 지어내지 않는다."""

    model: str
    apiKey: str = Field(min_length=1)
    modelRates: dict[str, ModelRateDTO] = Field(
        min_length=1,
        description="이 실행이 예산을 집행할 때 쓰는 서버 카탈로그의 모델별 단가다.",
    )
    limits: ExecutionLimitsDTO = Field(
        description="이 실행이 지킬 예산과 턴과 출력 상한이며 서버 카탈로그가 소유한다.",
    )
    jobId: str | None = None
    executionId: str | None = Field(
        default=None, description="백엔드 사이에서 같은 논리 실행을 잇는 식별자다."
    )
    attemptId: str | None = Field(default=None, description="논리 실행 안의 이번 시도를 잇는 식별자다.")
    deadlineMs: int = 120_000
    idempotencyKey: str | None = Field(
        default=None,
        description="같은 키의 성공한 실행 결과를 단일 프로세스의 제한된 시간 동안 재사용한다.",
    )
    fallbackModel: str | None = Field(
        default=None,
        description="primary 모델 호출이 공급자 오류로 실패했을 때 한 번만 대체할 모델이다.",
    )

    def idempotency_input_hash(self) -> str:
        """실행 제어·권한 창구를 제외한 도메인 입력의 안정 해시를 돌려준다."""
        payload = self.model_dump(
            mode="json",
            exclude={
                "apiKey",
                "jobId",
                "executionId",
                "attemptId",
                "deadlineMs",
                "idempotencyKey",
                "fallbackModel",
                "modelRates",
                "limits",
                "completionCallback",
                "draftCallback",
            },
        )
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def effective_fallback_model(self) -> str | None:
        """폴백 모델이 없거나 primary와 같으면 생략 대상임을 알린다."""
        if self.fallbackModel is None or self.fallbackModel == self.model:
            return None
        return self.fallbackModel


class AgentExecutionRequest(AgentExecutionEnvelope):
    """HTTP로 접수돼 결과를 완료 창구로 배달받는 실행 봉투다."""

    # 접수 스스로 진행 상황을 조회로 되읽는 호출은 완료 창구가 필요 없다.
    completionCallback: CompletionCallback | None = None


class AgentAccepted(BaseModel):
    """결과 본문은 완료 창구로 따로 전달되는 실행 접수 응답이다."""

    status: Literal["accepted"] = "accepted"
    runId: str


class UsageDTO(BaseModel):
    inputTokens: int
    outputTokens: int
    cacheReadTokens: int
    cacheCreationTokens: int


AgentStepRole = Literal["system", "user", "assistant", "tool", "orchestration"]
OrchestrationEventKind = Literal[
    "node.started",
    "node.completed",
    "node.failed",
    "route.selected",
    "validation.failed",
]


class AgentStepToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, object]


class AgentStepDTO(BaseModel):
    """언어 중립 실행 어휘 계약을 따르는 궤적 한 스텝이다."""

    seq: int
    role: AgentStepRole
    content: str
    truncated: bool = False
    toolCalls: list[AgentStepToolCall] = Field(default_factory=list)
    toolName: str | None = None
    toolCallId: str | None = None
    inputTokens: int | None = None
    outputTokens: int | None = None
    cacheReadTokens: int | None = None
    cacheCreationTokens: int | None = None
    stopReason: str | None = None
    nodeName: str | None = None
    eventKind: OrchestrationEventKind | None = None
    durationMs: int | None = None


class AgentErrorDTO(BaseModel):
    subtype: str | None = Field(description="값이 없으면 호출부의 기본 재시도 정책을 적용한다.")
    summary: str


class ObservationUsageDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)
    cacheReadTokens: int = Field(ge=0)
    cacheWriteTokens: int = Field(ge=0)


class ModelCallObservationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executionId: TrimmedStr
    attemptId: TrimmedStr
    modelCallId: TrimmedStr
    providerRequestId: str | None
    modelRequested: TrimmedStr
    modelActual: str | None
    status: Literal["succeeded", "failed", "cancelled"]
    durationMs: int = Field(ge=0)
    usage: ObservationUsageDTO
    costUsd: float | None = Field(default=None, ge=0)
    finishReason: str | None
    errorType: str | None


class ToolCallObservationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executionId: TrimmedStr
    attemptId: TrimmedStr
    toolCallId: TrimmedStr
    toolName: TrimmedStr
    status: Literal["succeeded", "failed", "cancelled"]
    durationMs: int = Field(ge=0)
    errorType: str | None


class ValidationObservationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    errorCodes: list[TrimmedStr]
    citationPrecision: float | None = Field(default=None, ge=0, le=1)
    citationRecall: float | None = Field(default=None, ge=0, le=1)


class AgentRunObservationDTO(BaseModel):
    """원문 prompt·출력·도구 payload·오류 본문을 담을 수 없는 terminal 실행 요약이다."""

    model_config = ConfigDict(extra="forbid")
    executionId: TrimmedStr
    attemptId: TrimmedStr
    jobId: str | None
    agentName: TrimmedStr
    backend: Literal["python"] = "python"
    modelRequested: TrimmedStr
    modelActual: str | None
    promptVersion: TrimmedStr
    promptContentHash: TrimmedStr
    toolContractVersion: TrimmedStr
    status: Literal["succeeded", "failed", "cancelled"]
    durationMs: int = Field(ge=0)
    usage: ObservationUsageDTO
    costUsd: float | None = Field(default=None, ge=0)
    landed: bool
    repairAttempted: bool
    validation: ValidationObservationDTO
    modelCalls: list[ModelCallObservationDTO]
    toolCalls: list[ToolCallObservationDTO]


from .agent_response import AgentResponse as AgentResponse  # noqa: E402
