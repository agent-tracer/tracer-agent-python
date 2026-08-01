"""잡 하나가 끝나며 남길 상태 전이와 궤적과 관측을 원장에 함께 적는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ...shared.agents.runtime.ledger import LedgerSql
from ...shared.agents.shared.models import AgentStepDTO
from ...shared.workflows.jobs_ledger import JobLedger

_INSERT_OBSERVATION = """
INSERT INTO agent_run_observations (
    execution_id, attempt_id, user_id, job_id,
    agent_name, backend, model_requested, model_actual, prompt_version,
    tool_contract_version, status, duration_ms, usage, cost_usd,
    landed, repair_attempted, validation, model_calls, tool_calls, created_at
)
SELECT
    payload->>'executionId', payload->>'attemptId', $1, payload->>'jobId',
    payload->>'agentName', payload->>'backend', payload->>'modelRequested', payload->>'modelActual',
    payload->>'promptVersion', payload->>'toolContractVersion',
    payload->>'status', (payload->>'durationMs')::integer,
    payload->'usage', (payload->>'costUsd')::double precision, (payload->>'landed')::boolean,
    (payload->>'repairAttempted')::boolean, payload->'validation', payload->'modelCalls',
    payload->'toolCalls', $3
FROM (SELECT $2::jsonb AS payload) source
ON CONFLICT (execution_id, attempt_id) DO NOTHING
RETURNING execution_id
"""


@dataclass(frozen=True)
class JobOutcome:
    """잡 하나가 남기고 끝나는 종료 상태와 산출과 사용량과 궤적이다."""

    job_id: str
    user_id: str
    status: str
    attempt: int
    result: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    steps: list[AgentStepDTO] = field(default_factory=list)
    observation: dict[str, Any] | None = None


class JobExecutionWriter:
    """잡의 종료 전이와 궤적과 관측을 한 트랜잭션으로 적는다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql
        self._ledger = JobLedger(sql)

    async def finalize(self, outcome: JobOutcome, now: datetime) -> bool:
        """잡의 종료 상태와 궤적과 관측을 함께 적고 전이가 받아들여졌는지 낸다."""
        async with self._sql.transaction():
            if outcome.observation is not None:
                await self._sql.fetch(_INSERT_OBSERVATION, outcome.user_id, outcome.observation, now)
            await self._ledger.record_steps(
                outcome.job_id, outcome.user_id, outcome.attempt, outcome.steps, now
            )
            return await self._ledger.settle(
                outcome.job_id, outcome.status, outcome.result, outcome.usage, outcome.error, now
            )
