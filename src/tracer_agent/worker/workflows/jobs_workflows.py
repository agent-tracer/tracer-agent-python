"""접수가 세운 잡 하나를 큐를 통해 워커에서 그래프째 돌리는 워크플로다."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import is_cancelled_exception

from ...shared.workflows.jobs_spec import (
    AGENT_JOB_WORKFLOW,
    JOB_CANCEL_SETTLE_TIMEOUT_S,
    JOB_HEARTBEAT_TIMEOUT_S,
    JOB_MAX_ATTEMPTS,
    JOB_TIMEOUT_S,
    RUN_AGENT_JOB_ACTIVITY,
    SETTLE_CANCELED_JOB_ACTIVITY,
    AgentJobRequest,
)


@workflow.defn(name=AGENT_JOB_WORKFLOW)
class AgentJobWorkflow:
    """title·recipe·cleanup 실행 하나를 재시도와 시간 초과가 걸린 액티비티 하나로 돌린다."""

    @workflow.run
    async def run(self, request: AgentJobRequest) -> None:
        """접수가 실어 보낸 요청 그대로 액티비티에 넘기며 결과는 완료 창구가 받는다."""
        try:
            await self._run_agent_job(request)
        except BaseException as error:
            # 액티비티가 시작도 못 하고 취소되면 원장 전이가 전혀 안 돌므로 워크플로가 직접 닫는다.
            if not is_cancelled_exception(error):
                raise
            await self._settle_canceled(request)
            raise

    async def _run_agent_job(self, request: AgentJobRequest) -> None:
        await workflow.execute_activity(
            RUN_AGENT_JOB_ACTIVITY,
            request,
            start_to_close_timeout=timedelta(seconds=JOB_TIMEOUT_S),
            heartbeat_timeout=timedelta(seconds=JOB_HEARTBEAT_TIMEOUT_S),
            # 기본값 TRY_CANCEL은 원장 정리 전에 취소를 확정해 종료 기록을 건너뛰므로 완료를 기다린다.
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            retry_policy=RetryPolicy(maximum_attempts=JOB_MAX_ATTEMPTS),
        )

    async def _settle_canceled(self, request: AgentJobRequest) -> None:
        """실행 식별자가 있을 때만 원장을 취소로 닫으며, 이미 착지한 행은 조건부 갱신이 그대로 둔다."""
        execution_id = request.payload.get("executionId")
        if not isinstance(execution_id, str) or not execution_id:
            return
        await workflow.execute_activity(
            SETTLE_CANCELED_JOB_ACTIVITY,
            execution_id,
            start_to_close_timeout=timedelta(seconds=JOB_CANCEL_SETTLE_TIMEOUT_S),
            retry_policy=RetryPolicy(maximum_attempts=JOB_MAX_ATTEMPTS),
        )
