"""잡 실행 원장 한 행과 그 궤적 여러 행을 조건부 갱신과 순서 있는 삽입으로 소유한다."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from ..agents.runtime.ledger import LedgerSql, SqlRow, UniqueViolation
from ..agents.shared.models import AgentStepDTO

_CLAIM = """
INSERT INTO ai_jobs (
    id, user_id, kind, executor, status, attempts, task_id, idempotency_key, input,
    created_at, updated_at
)
VALUES ($1, $2, $3, $4, 'pending', 0, $5, $6, $7, $8, $8)
ON CONFLICT (id) DO NOTHING
RETURNING id
"""

_MARK_RUNNING = """
UPDATE ai_jobs
   SET status = 'running',
       attempts = attempts + 1,
       started_at = COALESCE(started_at, $2),
       updated_at = $2
 WHERE id = $1 AND status IN ('pending', 'running')
RETURNING id
"""

_SETTLE = """
UPDATE ai_jobs
   SET status = $2, result = $3, usage = $4, error = $5, completed_at = $6, updated_at = $6
 WHERE id = $1 AND status IN ('pending', 'running')
RETURNING id
"""

_CANCEL = """
UPDATE ai_jobs
   SET status = 'canceled', completed_at = $2, updated_at = $2
 WHERE id = $1 AND status IN ('pending', 'running')
RETURNING id
"""

_FIND = "SELECT * FROM ai_jobs WHERE id = $1"

_INSERT_STEP = """
INSERT INTO ai_job_steps (
    id, job_id, user_id, attempt, seq, role, content, truncated, tool_calls, tool_name,
    tool_call_id, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
    stop_reason, node_name, event_kind, duration_ms, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
ON CONFLICT (job_id, attempt, seq) DO NOTHING
"""

_SELECT_STEPS = """
SELECT * FROM ai_job_steps
 WHERE job_id = $1 AND user_id = $2
 ORDER BY attempt ASC, seq ASC
"""


def carries_content(step: AgentStepDTO) -> bool:
    """텍스트도 도구 호출도 없는 스텝은 궤적에 아무 의미도 싣지 못한다."""
    return bool(step.content.strip()) or bool(step.toolCalls)


class JobLedger:
    """잡 실행 행을 조건부 갱신으로만 전진시키고 조건이 막으면 거짓을 낸다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def claim(
        self,
        job_id: str,
        user_id: str,
        kind: str,
        executor: str,
        task_id: str | None,
        idempotency_key: str | None,
        job_input: dict[str, Any],
        now: datetime,
    ) -> bool:
        """이 식별자나 이 멱등키의 행이 이미 있으면 새로 세우지 않고 거짓을 낸다."""
        try:
            rows = await self._sql.fetch(
                _CLAIM, job_id, user_id, kind, executor, task_id, idempotency_key, job_input, now
            )
        except UniqueViolation:
            # 다른 식별자가 같은 (user_id, kind, idempotency_key)를 이미 썼다는 뜻이다.
            return False
        return len(rows) == 1

    async def find(self, job_id: str) -> SqlRow | None:
        """잡 원장 행 하나를 있는 그대로 읽는다."""
        rows = await self._sql.fetch(_FIND, job_id)
        return rows[0] if rows else None

    async def mark_running(self, job_id: str, now: datetime) -> bool:
        """접수된 잡을 실행 중 자리로 옮기고 이번 시도를 시도 횟수에 더한다."""
        rows = await self._sql.fetch(_MARK_RUNNING, job_id, now)
        return len(rows) == 1

    async def settle(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any],
        usage: dict[str, Any],
        error: str | None,
        now: datetime,
    ) -> bool:
        """살아 있는 잡을 종료 상태와 산출과 사용량으로 닫는다."""
        rows = await self._sql.fetch(_SETTLE, job_id, status, result, usage, error, now)
        return len(rows) == 1

    async def cancel(self, job_id: str, now: datetime) -> bool:
        """아직 살아 있는 잡을 취소로 닫는다."""
        rows = await self._sql.fetch(_CANCEL, job_id, now)
        return len(rows) == 1

    async def record_steps(
        self, job_id: str, user_id: str, attempt: int, steps: list[AgentStepDTO], now: datetime
    ) -> None:
        """이번 시도가 남긴 궤적을 시도 회차와 순번으로 갈라 적는다."""
        for step in steps:
            if not carries_content(step):
                continue
            await self._sql.fetch(
                _INSERT_STEP,
                str(uuid.uuid4()),
                job_id,
                user_id,
                attempt,
                step.seq,
                step.role,
                step.content,
                step.truncated,
                [call.model_dump(mode="json") for call in step.toolCalls] or None,
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

    async def steps(self, job_id: str, user_id: str) -> list[SqlRow]:
        """그 잡이 남긴 궤적을 시도와 순번의 오름차순으로 읽는다."""
        return await self._sql.fetch(_SELECT_STEPS, job_id, user_id)
