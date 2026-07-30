"""접수가 파싱한 잡 요청 하나를 그 큐의 워크플로에 얹고 실행이 끝나기를 기다리지 않는다."""

from __future__ import annotations

from typing import Any

from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from .dispatch import TemporalClientProvider
from .jobs_spec import (
    AGENT_JOB_WORKFLOW,
    JOBS_TASK_QUEUE,
    AgentJobKind,
    AgentJobRequest,
    agent_job_workflow_id,
)


class TemporalJobDispatch:
    """접수가 연 Temporal 연결로 잡 워크플로 하나를 기동하고 접수 자체는 즉시 반환한다."""

    def __init__(self, provider: TemporalClientProvider) -> None:
        self._provider = provider

    async def start(self, kind: AgentJobKind, key: str, payload: dict[str, Any]) -> None:
        """같은 키로 이미 끝난 워크플로가 있어도 새 실행을 만들지 않고 그대로 접수를 성립시킨다."""
        client = await self._provider.client()
        try:
            await client.start_workflow(
                AGENT_JOB_WORKFLOW,
                AgentJobRequest(kind, payload),
                id=agent_job_workflow_id(kind, key),
                task_queue=JOBS_TASK_QUEUE,
                # 기본 정책은 완료된 워크플로의 같은 id 재제출을 새 실행으로 받아들이므로 명시로 막는다.
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return

    async def cancel(self, kind: AgentJobKind, key: str) -> bool:
        """큐에 있든 이미 잡혔든 이 잡 워크플로에 Temporal 취소를 걸고, 없으면 거짓을 낸다."""
        client = await self._provider.client()
        try:
            await client.get_workflow_handle(agent_job_workflow_id(kind, key)).cancel()
            return True
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                return False
            raise
