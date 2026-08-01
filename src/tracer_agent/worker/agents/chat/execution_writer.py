"""한 턴의 산출물을 실행 전이와 어시스턴트 메시지와 궤적으로 원장에 함께 적는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tracer_agent.shared.agents.chat.execution_ledger import ChatExecutionLedger, ChatExecutionSpend
from tracer_agent.shared.agents.chat.models import EXECUTION_BACKEND
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.runtime.wakeup import UpdatePublisher
from tracer_agent.shared.agents.shared.models import AgentRunObservationDTO, AgentStepDTO

_APPEND_ASSISTANT_MESSAGE = """
INSERT INTO chat_messages (id, thread_id, role, content, tool_calls, tool_call_id, created_at)
VALUES ($1, $2, 'assistant', $3, $4, NULL, $5)
ON CONFLICT (id) DO UPDATE
    SET content = excluded.content, tool_calls = excluded.tool_calls
RETURNING id
"""

_RECORD_THREAD_TURN = """
UPDATE chat_threads
   SET backend = $2, updated_at = $3
 WHERE id = $1 AND user_id = $4
RETURNING id
"""

_INSERT_STEP = """
INSERT INTO chat_execution_steps (
    id, execution_id, user_id, attempt, seq, role, content, truncated, tool_calls,
    tool_name, tool_call_id, input_tokens, output_tokens, cache_read_tokens,
    cache_creation_tokens, stop_reason, node_name, event_kind, duration_ms, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
ON CONFLICT (execution_id, attempt, seq) DO NOTHING
RETURNING id
"""

_INSERT_OBSERVATION = """
INSERT INTO agent_run_observations (
    execution_id, attempt_id, user_id, job_id,
    agent_name, backend, model_requested, model_actual, prompt_version, prompt_content_hash,
    tool_contract_version, status, duration_ms, usage, cost_usd,
    landed, repair_attempted, validation, model_calls, tool_calls, created_at
)
SELECT
    payload->>'executionId', payload->>'attemptId', $1, payload->>'jobId',
    payload->>'agentName', payload->>'backend', payload->>'modelRequested', payload->>'modelActual',
    payload->>'promptVersion', payload->>'promptContentHash', payload->>'toolContractVersion',
    payload->>'status', (payload->>'durationMs')::integer,
    payload->'usage', (payload->>'costUsd')::double precision, (payload->>'landed')::boolean,
    (payload->>'repairAttempted')::boolean, payload->'validation', payload->'modelCalls',
    payload->'toolCalls', $3
FROM (SELECT $2::jsonb AS payload) source
ON CONFLICT (execution_id, attempt_id) DO NOTHING
RETURNING execution_id
"""

_SELECT_OBSERVATION = """
SELECT *
  FROM agent_run_observations observation
 WHERE execution_id = $1 AND attempt_id = $2
"""


@dataclass(frozen=True)
class ChatTurnOutcome:
    """대화 턴 하나가 원장에 남길 산출물 전부다."""

    execution_id: str
    user_id: str
    thread_id: str
    attempt: int
    canceled: bool
    text: str
    spend: ChatExecutionSpend
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    steps: list[AgentStepDTO] = field(default_factory=list)
    observation: dict[str, Any] = field(default_factory=dict)


class ChatExecutionWriter:
    """산출물이 실행 전이와 함께 남거나 함께 사라지도록 한 트랜잭션으로 적는다."""

    def __init__(self, sql: LedgerSql, wakeup: UpdatePublisher | None = None) -> None:
        self._sql = sql
        self._ledger = ChatExecutionLedger(sql)
        self._wakeup = wakeup

    async def finalize(self, outcome: ChatTurnOutcome, now: datetime) -> bool:
        """턴의 산출물을 원장에 적고 실행 전이가 받아들여졌는지 낸다."""
        async with self._sql.transaction():
            await self._insert_observation(outcome, now)
            settled = await self._settle(outcome, now)
            if not settled:
                return False
            await self._append_message(outcome, now)
            for step in outcome.steps:
                await self._insert_step(outcome, step, now)
            await self._record_thread_turn(outcome, now)
        # 적히지 않은 갱신을 알리지 않도록 트랜잭션이 닫힌 뒤에만 깨운다.
        if self._wakeup is not None:
            await self._wakeup.publish(outcome.execution_id, {"executionId": outcome.execution_id})
        return True

    async def record_observation(
        self,
        execution_id: str,
        user_id: str,
        attempt: int,
        observation_payload: dict[str, Any],
        now: datetime,
    ) -> None:
        """실패로 끝난 시도도 잡 전체의 terminal 전이보다 먼저 안전한 관측을 남긴다."""
        async with self._sql.transaction():
            await self._insert_observation_values(execution_id, user_id, attempt, observation_payload, now)

    async def _settle(self, outcome: ChatTurnOutcome, now: datetime) -> bool:
        # 어시스턴트 메시지 식별자는 실행 식별자와 같아서 같은 턴을 다시 적어도 행이 늘지 않는다.
        if outcome.canceled:
            return await self._ledger.record_canceled_outcome(
                outcome.execution_id, outcome.execution_id, outcome.spend, now
            )
        return await self._ledger.complete_running(
            outcome.execution_id, outcome.execution_id, outcome.spend, now
        )

    async def _append_message(self, outcome: ChatTurnOutcome, now: datetime) -> None:
        await self._sql.fetch(
            _APPEND_ASSISTANT_MESSAGE,
            outcome.execution_id,
            outcome.thread_id,
            outcome.text,
            outcome.tool_calls or None,
            now,
        )

    async def _insert_observation(self, outcome: ChatTurnOutcome, now: datetime) -> None:
        await self._insert_observation_values(
            outcome.execution_id, outcome.user_id, outcome.attempt, outcome.observation, now
        )

    async def _insert_observation_values(
        self,
        execution_id: str,
        user_id: str,
        attempt: int,
        observation_payload: dict[str, Any],
        now: datetime,
    ) -> None:
        observation = AgentRunObservationDTO.model_validate(observation_payload)
        if observation.executionId != execution_id or observation.attemptId != str(attempt):
            raise ValueError("chat observation identity does not match terminal outcome")
        rows = await self._sql.fetch(_INSERT_OBSERVATION, user_id, observation.model_dump(mode="json"), now)
        if not rows:
            existing = await self._sql.fetch(
                _SELECT_OBSERVATION,
                execution_id,
                str(attempt),
            )
            if not existing or existing[0].get("user_id") != user_id:
                raise ValueError("chat terminal observation was not persisted")
            stored = AgentRunObservationDTO.model_validate(_observation_payload(existing[0]))
            if stored != observation:
                raise ValueError("chat terminal observation conflicts with immutable attempt")

    async def _record_thread_turn(self, outcome: ChatTurnOutcome, now: datetime) -> None:
        rows = await self._sql.fetch(
            _RECORD_THREAD_TURN, outcome.thread_id, EXECUTION_BACKEND, now, outcome.user_id
        )
        # 목록 정렬 기준을 밀지 못했다면 남의 스레드에 적으려 한 것이므로 턴 전체를 되감는다.
        if not rows:
            raise ValueError("chat thread not found for this user")

    async def _insert_step(self, outcome: ChatTurnOutcome, step: AgentStepDTO, now: datetime) -> None:
        await self._sql.fetch(
            _INSERT_STEP,
            step_id(outcome.execution_id, outcome.attempt, step.seq),
            outcome.execution_id,
            outcome.user_id,
            outcome.attempt,
            step.seq,
            step.role,
            step.content,
            step.truncated,
            [call.model_dump() for call in step.toolCalls] or None,
            step.toolName,
            step.toolCallId,
            step.inputTokens,
            step.outputTokens,
            step.cacheReadTokens,
            step.cacheCreationTokens,
            step.stopReason,
            step.nodeName,
            step.eventKind,
            step.durationMs,
            now,
        )


def step_id(execution_id: str, attempt: int, seq: int) -> str:
    """같은 시도의 같은 순번을 다시 적어도 행이 늘지 않도록 궤적 식별자를 자리에서 만든다."""
    return f"{execution_id}:{attempt}:{seq}"


def _observation_payload(row: dict[str, Any]) -> dict[str, Any]:
    """DB의 snake_case 행을 언어 중립 canonical 계약으로 되돌린다."""
    return {
        "executionId": row["execution_id"],
        "attemptId": row["attempt_id"],
        "jobId": row["job_id"],
        "agentName": row["agent_name"],
        "backend": row["backend"],
        "modelRequested": row["model_requested"],
        "modelActual": row["model_actual"],
        "promptVersion": row["prompt_version"],
        "promptContentHash": row["prompt_content_hash"],
        "toolContractVersion": row["tool_contract_version"],
        "status": row["status"],
        "durationMs": row["duration_ms"],
        "usage": row["usage"],
        "costUsd": row["cost_usd"],
        "landed": bool(row["landed"]),
        "repairAttempted": bool(row["repair_attempted"]),
        "validation": row["validation"],
        "modelCalls": row["model_calls"],
        "toolCalls": row["tool_calls"],
    }
