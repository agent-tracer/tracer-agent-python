"""에이전트 본체에 데드라인·관측·오류 응답 조립을 적용한다."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from opentelemetry.context import Context

from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.models import (
    AgentErrorDTO,
    AgentResponse,
    UsageDTO,
)

from ..errors import CANCELLED, INVALID_REQUEST_ERROR, DeadlineExceeded, classify_exception
from ..telemetry.attributes import apply_usage_attributes
from ..telemetry.execution_metrics import record_model_landed
from ..telemetry.metrics import record_client_metrics
from ..telemetry.spans import invoke_agent_span, mark_span_error
from .registry import IdempotencyConflict, run_registered
from .trace import ExecutionTrace

AgentBody = Callable[[ExecutionTrace], Awaitable[JsonObject]]


async def execute(
    label: str,
    model: str,
    deadline_ms: int,
    body: AgentBody,
    job_id: str | None = None,
    idempotency_key: str | None = None,
    run_id: str | None = None,
    parent_context: Context | None = None,
    input_hash: str = "",
    execution_id: str | None = None,
    attempt_id: str | None = None,
    *,
    prompt_version: str,
    tool_contract_version: str,
) -> AgentResponse:
    """에이전트 실행을 등록하고 표준 응답으로 돌려준다."""
    key = run_id or idempotency_key or job_id
    try:
        return await run_registered(
            lambda: _execute(
                label,
                model,
                deadline_ms,
                body,
                job_id,
                parent_context,
                execution_id,
                attempt_id or "1",
                prompt_version=prompt_version,
                tool_contract_version=tool_contract_version,
            ),
            label=label,
            model=model,
            job_id=job_id,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            run_key=key,
        )
    except IdempotencyConflict as err:
        return AgentResponse(
            modelUsed=model,
            durationMs=0,
            error=AgentErrorDTO(subtype=INVALID_REQUEST_ERROR, summary=str(err)),
        )


async def _execute(
    label: str,
    model: str,
    deadline_ms: int,
    body: AgentBody,
    job_id: str | None = None,
    parent_context: Context | None = None,
    execution_id: str | None = None,
    attempt_id: str = "1",
    *,
    prompt_version: str,
    tool_contract_version: str,
) -> AgentResponse:
    started = time.monotonic()
    trace = ExecutionTrace()
    data: JsonObject | None = None
    error = None
    usage_dto: UsageDTO | None = None
    async with invoke_agent_span(
        job_id=job_id,
        agent_name=label,
        model=model,
        parent_context=parent_context,
    ) as span:
        try:
            data = await asyncio.wait_for(body(trace), timeout=max(0.001, deadline_ms / 1000))
        except TimeoutError:
            error = classify_exception(DeadlineExceeded(f"agent {label} exceeded {deadline_ms}ms"))
        except asyncio.CancelledError:
            if execution_id is None:
                raise
            error = AgentErrorDTO(subtype=CANCELLED, summary="agent execution cancelled")
        except BaseException as err:
            # classify_exception은 요약 문자열만 남기므로 원인 추적은 스팬의 exception 레코드에 맡긴다.
            from ..errors import _redact_exception

            error = classify_exception(err)
            span.record_exception(_redact_exception(err))
        usage_dto = trace.to_usage_dto()
        apply_usage_attributes(span, usage_dto)
        if error is not None:
            mark_span_error(span, error.subtype, error.summary)

    duration_ms = int((time.monotonic() - started) * 1000)
    error_subtype = error.subtype if error else None
    record_client_metrics(model, duration_ms / 1000, usage_dto, error_subtype)
    if trace.landed:
        record_model_landed(label)
    observation = (
        None
        if execution_id is None
        else trace.to_observation(
            execution_id=execution_id,
            attempt_id=attempt_id,
            job_id=job_id,
            agent_name=label,
            model_requested=model,
            prompt_version=prompt_version,
            tool_contract_version=tool_contract_version,
            duration_ms=duration_ms,
            ttft_ms=_ttft_ms(trace.first_token_at, started),
            error_subtype=error_subtype,
        )
    )
    return AgentResponse(
        data=data,
        modelUsed=model,
        durationMs=duration_ms,
        numTurns=trace.turns or None,
        usage=usage_dto,
        error=error,
        steps=trace.steps,
        landed=trace.landed,
        actualModel=trace.actual_model,
        providerRequestId=trace.provider_request_id,
        observation=observation,
    )


def _ttft_ms(first_token_at: float | None, started: float) -> int | None:
    """첫 조각을 받은 실행만 값을 가지며 받지 못한 실행은 비운 채로 둔다."""
    if first_token_at is None:
        return None
    return max(0, int((first_token_at - started) * 1000))
