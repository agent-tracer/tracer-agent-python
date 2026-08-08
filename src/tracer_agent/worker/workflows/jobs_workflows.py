"""접수가 세운 잡 하나를 준비와 생성과 종결로 나눠 워커에서 실행하는 워크플로다."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import is_cancelled_exception

from ...shared.agents.shared.json_view import JsonObject

# 종결 값의 모양은 계약 파일을 읽어 세워지므로 샌드박스가 그 모듈을 다시 실행하지 않게 그대로 들여온다.
with workflow.unsafe.imports_passed_through():
    from ...shared.workflows.jobs_spec import (
        AGENT_JOB_WORKFLOW,
        FAIL_AGENT_JOB_ACTIVITY,
        FINALIZE_AGENT_JOB_ACTIVITY,
        GENERATE_AGENT_JOB_ACTIVITY,
        GENERATE_TASK_QUEUE,
        JOB_CANCEL_SETTLE_TIMEOUT_S,
        JOB_FINALIZE_MAX_ATTEMPTS,
        JOB_FINALIZE_TIMEOUT_S,
        JOB_GENERATE_MAX_ATTEMPTS,
        JOB_GENERATE_SCHEDULE_TO_CLOSE_S,
        JOB_GENERATE_TIMEOUT_S,
        JOB_HEARTBEAT_TIMEOUT_S,
        JOB_PREPARE_MAX_ATTEMPTS,
        JOB_PREPARE_TIMEOUT_S,
        PREPARE_AGENT_JOB_ACTIVITY,
        SETTLE_CANCELED_JOB_ACTIVITY,
        AgentJobRequest,
        AgentJobSettlement,
        GeneratedAgentJob,
    )


@workflow.defn(name=AGENT_JOB_WORKFLOW)
class AgentJobWorkflow:
    """title·recipe·cleanup 실행 하나를 준비와 생성과 종결 액티비티로 나눠 실행한다."""

    @workflow.run
    async def run(self, request: AgentJobRequest) -> None:
        """단계마다 자기 재시도 경계를 가지며 모델을 부르는 생성만 분리한 큐에서 실행된다."""
        try:
            prepared = await self._prepare(request)
            generated = await self._generate(AgentJobRequest(kind=request.kind, payload=prepared))
            # 종결이 취소 뒤에도 끝까지 실행돼야 이번 시도의 궤적과 관측과 사용량이 원장에 남는다.
            await _closing(
                self._finalize(
                    AgentJobSettlement(
                        kind=request.kind,
                        payload=prepared,
                        outcome=generated.outcome,
                        response=generated.response,
                    )
                )
            )
        except BaseException as error:
            # 액티비티가 시작도 못 하고 끝나면 원장 전이가 전혀 안 돌므로 워크플로가 직접 닫는다.
            if is_cancelled_exception(error):
                await _closing(self._settle_canceled(request))
            else:
                await _closing(self._fail(request, str(error)))
            raise

    async def _prepare(self, request: AgentJobRequest) -> JsonObject:
        prepared: JsonObject = await workflow.execute_activity(
            PREPARE_AGENT_JOB_ACTIVITY,
            request,
            start_to_close_timeout=timedelta(seconds=JOB_PREPARE_TIMEOUT_S),
            retry_policy=RetryPolicy(maximum_attempts=JOB_PREPARE_MAX_ATTEMPTS),
        )
        return prepared

    async def _generate(self, request: AgentJobRequest) -> GeneratedAgentJob:
        generated: GeneratedAgentJob = await workflow.execute_activity(
            GENERATE_AGENT_JOB_ACTIVITY,
            request,
            result_type=GeneratedAgentJob,
            task_queue=GENERATE_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=JOB_GENERATE_TIMEOUT_S),
            schedule_to_close_timeout=timedelta(seconds=JOB_GENERATE_SCHEDULE_TO_CLOSE_S),
            heartbeat_timeout=timedelta(seconds=JOB_HEARTBEAT_TIMEOUT_S),
            # 기본값 TRY_CANCEL은 원장 정리 전에 취소를 확정해 종료 기록을 건너뛰므로 완료를 기다린다.
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            retry_policy=RetryPolicy(maximum_attempts=JOB_GENERATE_MAX_ATTEMPTS),
        )
        return generated

    async def _finalize(self, settlement: AgentJobSettlement) -> None:
        await workflow.execute_activity(
            FINALIZE_AGENT_JOB_ACTIVITY,
            settlement,
            start_to_close_timeout=timedelta(seconds=JOB_FINALIZE_TIMEOUT_S),
            retry_policy=RetryPolicy(maximum_attempts=JOB_FINALIZE_MAX_ATTEMPTS),
        )

    async def _fail(self, request: AgentJobRequest, message: str) -> None:
        """어느 단계가 실패하든 원장이 그 실행을 실패로 닫는다."""
        await workflow.execute_activity(
            FAIL_AGENT_JOB_ACTIVITY,
            args=[request, message],
            start_to_close_timeout=timedelta(seconds=JOB_FINALIZE_TIMEOUT_S),
            retry_policy=RetryPolicy(maximum_attempts=JOB_FINALIZE_MAX_ATTEMPTS),
        )

    async def _settle_canceled(self, request: AgentJobRequest) -> None:
        """실행 식별자가 있을 때만 원장을 취소로 닫으며, 이미 종료한 행은 조건부 갱신이 그대로 둔다."""
        execution_id = request.payload.get("executionId")
        if not isinstance(execution_id, str) or not execution_id:
            return
        await workflow.execute_activity(
            SETTLE_CANCELED_JOB_ACTIVITY,
            execution_id,
            start_to_close_timeout=timedelta(seconds=JOB_CANCEL_SETTLE_TIMEOUT_S),
            retry_policy=RetryPolicy(maximum_attempts=JOB_FINALIZE_MAX_ATTEMPTS),
        )


async def _closing(closing: Coroutine[Any, Any, None]) -> None:
    """원장을 닫는 액티비티는 취소가 이미 걸린 스코프에서도 끝까지 돌아야 한다."""
    running = asyncio.ensure_future(closing)
    try:
        await asyncio.shield(running)
    except asyncio.CancelledError:
        await running
        raise
