"""에이전트 본체에 데드라인·관측·오류 응답 조립을 적용한다."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.models import (
    AgentErrorDTO,
    AgentResponse,
    UsageDTO,
)

from ..errors import CANCELLED, DeadlineExceeded, classify_exception, redact_exception
from ..telemetry.attributes import apply_usage_attributes
from ..telemetry.execution_metrics import record_model_landed
from ..telemetry.metrics import record_client_metrics
from ..telemetry.spans import invoke_agent_span, mark_span_error
from .trace import ExecutionTrace

AgentBody = Callable[[ExecutionTrace], Awaitable[JsonObject]]


@dataclass(frozen=True)
class ExecutionRequest:
    """실행 하나를 관측과 원장에 잇는 이름과 판과 벽시계 상한이며 본문은 싣지 않는다."""

    label: str
    model: str
    deadline_ms: int
    prompt_version: str
    tool_contract_version: str
    job_id: str | None = None
    execution_id: str | None = None
    attempt_id: str | None = None

    def attempt(self) -> str:
        """관측이 적는 시도 회차이며 요청이 회차를 싣지 않으면 첫 시도로 본다."""
        return self.attempt_id or "1"


async def execute(
    request: ExecutionRequest,
    body: AgentBody,
    trace: ExecutionTrace | None = None,
) -> AgentResponse:
    """에이전트 본체를 데드라인 안에서 실행해 표준 응답을 내며 궤적은 호출자가 넘긴 것에 쌓는다."""
    started = time.monotonic()
    run_trace = ExecutionTrace() if trace is None else trace
    data: JsonObject | None = None
    error = None
    usage_dto: UsageDTO | None = None
    async with invoke_agent_span(
        job_id=request.job_id,
        agent_name=request.label,
        model=request.model,
    ) as span:
        try:
            data = await asyncio.wait_for(body(run_trace), timeout=max(0.001, request.deadline_ms / 1000))
        except TimeoutError:
            error = classify_exception(
                DeadlineExceeded(f"agent {request.label} exceeded {request.deadline_ms}ms")
            )
        except asyncio.CancelledError:
            if request.execution_id is None:
                raise
            error = AgentErrorDTO(subtype=CANCELLED, summary="agent execution cancelled")
        except BaseException as err:
            # classify_exception은 요약 문자열만 남기므로 원인 추적은 스팬의 exception 레코드에 맡긴다.
            error = classify_exception(err)
            span.record_exception(redact_exception(err))
        usage_dto = run_trace.to_usage_dto()
        apply_usage_attributes(span, usage_dto)
        if error is not None:
            mark_span_error(span, error.subtype, error.summary)

    duration_ms = int((time.monotonic() - started) * 1000)
    error_subtype = error.subtype if error else None
    record_client_metrics(request.model, run_trace.actual_model, duration_ms / 1000, usage_dto, error_subtype)
    if run_trace.landed:
        record_model_landed(request.label)
    observation = (
        None
        if request.execution_id is None
        else run_trace.to_observation(
            execution_id=request.execution_id,
            attempt_id=request.attempt(),
            job_id=request.job_id,
            agent_name=request.label,
            model_requested=request.model,
            prompt_version=request.prompt_version,
            tool_contract_version=request.tool_contract_version,
            duration_ms=duration_ms,
            ttft_ms=_ttft_ms(run_trace.first_token_at, started),
            error_subtype=error_subtype,
        )
    )
    return AgentResponse(
        data=data,
        modelUsed=request.model,
        durationMs=duration_ms,
        numTurns=run_trace.turns or None,
        usage=usage_dto,
        error=error,
        steps=run_trace.steps,
        landed=run_trace.landed,
        actualModel=run_trace.actual_model,
        providerRequestId=run_trace.provider_request_id,
        observation=observation,
    )


def _ttft_ms(first_token_at: float | None, started: float) -> int | None:
    """첫 조각을 받은 실행만 값을 가지며 받지 못한 실행은 비운 채로 둔다."""
    if first_token_at is None:
        return None
    return max(0, int((first_token_at - started) * 1000))
