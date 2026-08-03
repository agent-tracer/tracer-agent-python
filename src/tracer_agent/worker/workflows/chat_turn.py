"""한 턴의 실행 결과를 원장이 받을 산출물과 지출로 옮기고 다시 실행할 실패인지 구분한다."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from ...shared.agents.chat.execution_ledger import ChatExecutionSpend
from ...shared.agents.chat.models import ChatRequest
from ...shared.agents.shared.json_view import JsonObject
from ...shared.agents.shared.models import AgentResponse, AgentStepDTO, UsageDTO
from ...shared.workflows.chat_spec import (
    STOP_BUDGET_LANDED,
    STOP_CANCELED,
    STOP_COMPLETED,
    GeneratedChatExecution,
    PreparedChatExecution,
)
from ..agents.chat.execution_writer import ChatTurnOutcome
from ..agents.runtime.execution.trace import ExecutionTrace
from ..agents.runtime.pricing import ModelRates
from ..agents.shared.prompt_source_port import AgentPrompt

INVALID_ENVELOPE = "chat.invalid-envelope"
GENERATE_FAILED = "chat.generate-failed"

# 같은 봉투로 다시 불러도 같은 자리에서 끝나는 실패이며, 공급자가 준 낯선 이름은 재시도로 떨어진다.
NON_RETRYABLE_SUBTYPES = frozenset(
    {
        "deadline_exceeded",
        "budget_exceeded",
        "max_tokens",
        "max_turns_exceeded",
        "invalid_request_error",
        "agent_execution_error",
    }
)


def turn_request(prepared: PreparedChatExecution, envelope: dict[str, Any]) -> ChatRequest:
    """접수가 실어 보낸 봉투를 원장이 든 사실로 덮어 이번 턴의 실행 요청을 만든다."""
    payload = dict(envelope)
    payload["executionId"] = prepared.execution_id
    payload["threadId"] = prepared.thread_id
    payload["userId"] = prepared.user_id
    payload["language"] = prepared.language
    if prepared.model:
        payload["model"] = prepared.model
    try:
        return ChatRequest.model_validate(payload)
    except ValidationError as error:
        raise ApplicationError(str(error), type=INVALID_ENVELOPE, non_retryable=True) from error


def raise_for_error(response: AgentResponse) -> None:
    """실행이 오류로 끝났으면 그 서브타입의 재시도 판정과 함께 워크플로에 올린다."""
    if response.error is None:
        return
    subtype = response.error.subtype or ""
    raise ApplicationError(
        response.error.summary,
        type=subtype or GENERATE_FAILED,
        non_retryable=subtype in NON_RETRYABLE_SUBTYPES,
    )


def canceled_turn(
    prepared: PreparedChatExecution,
    attempt: int,
    request: ChatRequest,
    trace: ExecutionTrace,
    prompt: AgentPrompt,
) -> GeneratedChatExecution:
    """취소로 끊긴 턴이 그때까지 쌓은 답변과 궤적과 지출을 산출물로 낸다."""
    return _turn(
        prepared,
        attempt,
        request,
        canceled=True,
        text=_last_assistant_text(trace.steps),
        stop_reason=STOP_CANCELED,
        steps=trace.steps,
        usage=trace.to_usage_dto(),
        num_turns=trace.turns or None,
        model_used=trace.actual_model or request.model,
        observation=trace.to_observation(
            execution_id=prepared.execution_id,
            attempt_id=str(attempt),
            job_id=None,
            agent_name="chat",
            model_requested=request.model,
            prompt_version=prompt.version(),
            tool_contract_version=prompt.tool_contract_version,
            duration_ms=0,
            # 이 산출은 실행을 잰 자리 밖에서 만들어지므로 잰 값을 옮겨 적을 기준 시각이 없다.
            ttft_ms=None,
            error_subtype="cancelled",
        ).model_dump(mode="json"),
    )


def completed_turn(
    prepared: PreparedChatExecution,
    attempt: int,
    request: ChatRequest,
    response: AgentResponse,
) -> GeneratedChatExecution:
    """끝까지 돈 턴의 답변과 확인 대기 행 인용과 지출을 산출물로 낸다."""
    data = response.data or {}
    text = str(data.get("assistantText", ""))
    if not text.strip():
        raise RuntimeError("chat turn produced no assistant response")
    generated = _turn(
        prepared,
        attempt,
        request,
        canceled=False,
        text=text,
        stop_reason=STOP_BUDGET_LANDED if response.landed else STOP_COMPLETED,
        steps=response.steps,
        usage=response.usage,
        num_turns=response.numTurns,
        model_used=response.actualModel or response.modelUsed,
        observation=(response.observation.model_dump(mode="json") if response.observation else {}),
    )
    generated.tool_calls = _tool_calls(data)
    return generated


def ledger_outcome(generated: GeneratedChatExecution) -> ChatTurnOutcome:
    """워크플로가 나른 산출물을 원장 쓰기가 받는 턴 산출물로 되돌린다."""
    return ChatTurnOutcome(
        execution_id=generated.execution_id,
        user_id=generated.user_id,
        thread_id=generated.thread_id,
        attempt=generated.attempt,
        canceled=generated.canceled,
        text=generated.text,
        spend=ChatExecutionSpend(
            model_used=generated.model_used,
            cost_usd=generated.cost_usd,
            num_turns=generated.num_turns,
            stop_reason=generated.stop_reason,
            usage=generated.usage,
        ),
        tool_calls=generated.tool_calls,
        steps=[AgentStepDTO.model_validate(step) for step in generated.steps],
        observation=generated.observation,
    )


def _turn(
    prepared: PreparedChatExecution,
    attempt: int,
    request: ChatRequest,
    *,
    canceled: bool,
    text: str,
    stop_reason: str,
    steps: list[AgentStepDTO],
    usage: UsageDTO | None,
    num_turns: int | None,
    model_used: str,
    observation: dict[str, Any],
) -> GeneratedChatExecution:
    return GeneratedChatExecution(
        execution_id=prepared.execution_id,
        thread_id=prepared.thread_id,
        user_id=prepared.user_id,
        attempt=attempt,
        canceled=canceled,
        text=text,
        model_used=model_used,
        stop_reason=stop_reason,
        # 저장용 지출도 실행 봉투가 실어 온 단가로만 환산하며 이 서비스가 단가표를 갖지 않는다.
        cost_usd=ModelRates(request.modelRates).estimate_cost_usd(model_used, usage),
        num_turns=num_turns,
        usage={} if usage is None else usage.model_dump(mode="json"),
        steps=[step.model_dump(mode="json") for step in steps],
        observation=observation,
    )


def _tool_calls(data: JsonObject) -> list[dict[str, Any]]:
    proposed = data.get("proposedWrites")
    if not isinstance(proposed, list):
        return []
    return [
        {"id": write["confirmationId"], "name": write["toolName"], "args": write.get("args", {})}
        for write in proposed
        if isinstance(write, dict)
    ]


def _last_assistant_text(steps: list[AgentStepDTO]) -> str:
    for step in reversed(steps):
        if step.role == "assistant" and step.content:
            return step.content
    return ""
