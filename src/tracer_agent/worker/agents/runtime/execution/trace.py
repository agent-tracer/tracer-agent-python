"""에이전트 실행 전체의 사용량과 반환 궤적을 소유한다."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from tracer_agent.shared.agents.shared.models import (
    AgentRunObservationDTO,
    AgentStepDTO,
    ModelCallObservationDTO,
    ObservationStatus,
    ObservationUsageDTO,
    OrchestrationEventKind,
    ToolCallObservationDTO,
    UsageDTO,
    ValidationObservationDTO,
)

from ..llm.trajectory import (
    cap_step_content,
    extract_token_usage,
    message_identity,
    message_step,
)
from ..routes import REPAIR


@dataclass
class ExecutionTrace:
    """한 에이전트 실행에서 관측한 사용량과 단계를 누적한다."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    turns: int = 0
    seen: bool = field(default=False)
    landed: bool = field(default=False)
    steps: list[AgentStepDTO] = field(default_factory=list)
    actual_model: str | None = None
    provider_request_id: str | None = None
    first_token_at: float | None = None
    # 실패로 돌아온 도구 호출이며 관측이 이것을 성공으로 세지 않도록 궤적이 따로 기억한다.
    failed_tool_calls: set[str] = field(default_factory=set)

    def mark_first_token(self) -> None:
        """스트리밍이 첫 조각을 내놓은 시각을 한 번만 남긴다."""
        if self.first_token_at is None:
            self.first_token_at = time.monotonic()

    def add_message(self, message: BaseMessage) -> None:
        """AI 응답의 모델 식별자와 토큰 사용량을 더한다."""
        if not isinstance(message, AIMessage):
            return
        self.turns += 1
        self._remember_identity(message)
        usage = extract_token_usage(message)
        if usage is None:
            return
        self.seen = True
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.cache_creation_tokens += usage.cache_creation_tokens

    def mark_landed(self) -> None:
        """조사 도구를 거두고 결론만 받는 마지막 호출로 넘어갔음을 남긴다."""
        self.landed = True

    def to_usage_dto(self) -> UsageDTO | None:
        """누적한 토큰이 있을 때 응답 사용량 계약을 만든다."""
        if not self.seen:
            return None
        return UsageDTO(
            inputTokens=self.input_tokens,
            outputTokens=self.output_tokens,
            cacheReadTokens=self.cache_read_tokens,
            cacheCreationTokens=self.cache_creation_tokens,
        )

    def record_message(self, message: BaseMessage) -> None:
        """모델 대화 메시지를 반환 가능한 실행 단계로 기록하며 본문도 도구 호출도 없으면 버린다."""
        self._remember_identity(message)
        if isinstance(message, ToolMessage) and message.status == "error" and message.tool_call_id:
            self.failed_tool_calls.add(message.tool_call_id)
        step = message_step(message, len(self.steps))
        if not step.carries_content():
            return
        self.steps.append(step)

    def record_orchestration_event(
        self,
        event_kind: OrchestrationEventKind,
        content: str,
        *,
        node_name: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """실행을 엮는 층의 노드·분기·검증 이벤트를 실행 단계로 기록한다."""
        normalized = content.strip()
        if not normalized:
            raise ValueError("orchestration event content must not be empty")
        capped, truncated = cap_step_content(normalized)
        self.steps.append(
            AgentStepDTO(
                seq=len(self.steps),
                role="orchestration",
                content=capped,
                truncated=truncated,
                nodeName=node_name,
                eventKind=event_kind,
                durationMs=duration_ms,
            )
        )

    def _remember_identity(self, message: BaseMessage) -> None:
        actual_model, request_id = message_identity(message)
        self.actual_model = actual_model or self.actual_model
        self.provider_request_id = request_id or self.provider_request_id

    def to_observation(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        job_id: str | None,
        agent_name: str,
        model_requested: str,
        prompt_version: str,
        tool_contract_version: str,
        duration_ms: int,
        ttft_ms: int | None,
        error_subtype: str | None,
    ) -> AgentRunObservationDTO:
        """원문 content와 도구 payload를 버리고 공통 terminal observation만 만든다."""
        status: ObservationStatus = (
            "succeeded"
            if error_subtype is None
            else ("cancelled" if error_subtype == "cancelled" else "failed")
        )
        usage = ObservationUsageDTO(
            inputTokens=self.input_tokens,
            outputTokens=self.output_tokens,
            cacheReadTokens=self.cache_read_tokens,
            cacheWriteTokens=self.cache_creation_tokens,
        )
        actual_model = self.actual_model
        model_call = ModelCallObservationDTO(
            executionId=execution_id,
            attemptId=attempt_id,
            modelCallId=f"{execution_id}:{attempt_id}:aggregate-model-call",
            providerRequestId=self.provider_request_id,
            modelRequested=model_requested,
            modelActual=actual_model,
            status=status,
            durationMs=duration_ms,
            usage=usage,
            costUsd=None,
            finishReason=_last_finish_reason(self.steps),
            errorType=error_subtype,
        )
        return AgentRunObservationDTO(
            executionId=execution_id,
            attemptId=attempt_id,
            jobId=job_id,
            agentName=agent_name,
            modelRequested=model_requested,
            modelActual=actual_model,
            promptVersion=prompt_version,
            toolContractVersion=tool_contract_version,
            status=status,
            durationMs=duration_ms,
            ttftMs=ttft_ms,
            usage=usage,
            landed=self.landed,
            repairAttempted=any(step.nodeName == REPAIR for step in self.steps),
            validation=ValidationObservationDTO(
                passed=error_subtype is None,
                errorCodes=[] if error_subtype is None else [error_subtype],
            ),
            modelCalls=[model_call],
            toolCalls=_tool_observations(
                self.steps, execution_id, attempt_id, status, self.failed_tool_calls
            ),
        )


def _last_finish_reason(steps: list[AgentStepDTO]) -> str | None:
    for step in reversed(steps):
        if step.stopReason:
            return step.stopReason
    return None


# 도구 실패를 사유와 함께 모델에게 전달했을 때 관측이 그 호출에 적는 오류 종류다.
TOOL_EXECUTION_ERROR = "tool_execution_error"
# 모델이 부르기만 하고 결과가 궤적에 남지 않은 호출에 관측이 적는 오류 종류다.
INCOMPLETE_TOOL_CALL = "incomplete_tool_call"


def _tool_observations(
    steps: list[AgentStepDTO],
    execution_id: str,
    attempt_id: str,
    status: ObservationStatus,
    failed: set[str],
) -> list[ToolCallObservationDTO]:
    names: dict[str, str] = {}
    completed: set[str] = set()
    results: list[ToolCallObservationDTO] = []
    for step in steps:
        for call in step.toolCalls:
            names[call.id] = call.name
        if step.role != "tool" or step.toolCallId is None or not step.toolName:
            continue
        completed.add(step.toolCallId)
        broke = step.toolCallId in failed
        results.append(
            ToolCallObservationDTO(
                executionId=execution_id,
                attemptId=attempt_id,
                toolCallId=step.toolCallId,
                toolName=step.toolName,
                status="failed" if broke else "succeeded",
                durationMs=step.durationMs or 0,
                errorType=TOOL_EXECUTION_ERROR if broke else None,
            )
        )
    for tool_call_id, tool_name in names.items():
        if tool_call_id in completed:
            continue
        results.append(
            ToolCallObservationDTO(
                executionId=execution_id,
                attemptId=attempt_id,
                toolCallId=tool_call_id,
                toolName=tool_name,
                status=status,
                durationMs=0,
                errorType=None if status == "succeeded" else INCOMPLETE_TOOL_CALL,
            )
        )
    return results
