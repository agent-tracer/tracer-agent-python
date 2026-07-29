"""잡 하나가 끝나며 남길 상태 전이와 관측을 원장에 함께 적는다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...shared.agents.runtime.ledger import LedgerSql
from ...shared.workflows.jobs_ledger import GraphJobLedger

_INSERT_OBSERVATION = """
INSERT INTO agent_run_observations (
    execution_id, attempt_id, user_id, job_id, experiment_id, example_id, variant_id,
    agent_name, backend, model_requested, model_actual, prompt_version, prompt_content_hash,
    tool_contract_version, evaluator_set_version, status, duration_ms, usage, cost_usd,
    landed, repair_attempted, validation, model_calls, tool_calls, created_at
)
SELECT
    payload->>'executionId', payload->>'attemptId', $1, payload->>'jobId',
    payload->>'experimentId', payload->>'exampleId', payload->>'variantId',
    payload->>'agentName', payload->>'backend', payload->>'modelRequested', payload->>'modelActual',
    payload->>'promptVersion', payload->>'promptContentHash', payload->>'toolContractVersion',
    payload->>'evaluatorSetVersion', payload->>'status', (payload->>'durationMs')::integer,
    payload->'usage', (payload->>'costUsd')::double precision, (payload->>'landed')::boolean,
    (payload->>'repairAttempted')::boolean, payload->'validation', payload->'modelCalls',
    payload->'toolCalls', $3
FROM (SELECT $2::jsonb AS payload) source
ON CONFLICT (execution_id, attempt_id) DO NOTHING
RETURNING execution_id
"""


class GraphJobExecutionWriter:
    """잡의 종료 전이와 관측을 한 트랜잭션으로 적는다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql
        self._ledger = GraphJobLedger(sql)

    async def finalize(
        self,
        execution_id: str,
        user_id: str,
        status: str,
        cost_usd: float | None,
        error: str | None,
        observation_payload: dict[str, Any] | None,
        now: datetime,
        result: dict[str, Any] | None = None,
    ) -> bool:
        """잡의 종료 상태와 지출과 구조화 결과를 관측과 함께 적고 전이가 받아들여졌는지 낸다."""
        async with self._sql.transaction():
            if observation_payload is not None:
                await self._sql.fetch(_INSERT_OBSERVATION, user_id, observation_payload, now)
            return await self._ledger.settle(execution_id, status, cost_usd, error, now, result)
