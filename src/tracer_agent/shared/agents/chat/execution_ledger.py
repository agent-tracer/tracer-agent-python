"""chat 실행 원장의 상태 전이를 조건부 갱신 한 문장씩으로 소유한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..runtime.ledger import LedgerSql, SqlRow, UniqueViolation
from ..shared.axis import AGENT_BACKEND

CLAIMED = "claimed"
STALE = "stale"
THREAD_BUSY = "thread-busy"

_SELECT = "SELECT * FROM chat_executions WHERE id = $1"

_SELECT_ACTIVE = """
SELECT id, thread_id FROM chat_executions
 WHERE status IN ('queued', 'running')
   AND requested_backend = $1
 ORDER BY id
"""

_SELECT_NEXT_QUEUED_IN_THREAD = """
SELECT id FROM chat_executions
 WHERE thread_id = $1
   AND status = 'queued'
   AND requested_backend = $2
 ORDER BY created_at ASC, id ASC
 LIMIT 1
"""

_RECOVER_STALE_RUNNING = """
UPDATE chat_executions
   SET status = 'queued', started_at = NULL, updated_at = $1
 WHERE status = 'running' AND updated_at < $2 AND requested_backend = $3
RETURNING id
"""

_RECOVER_STALE_RUNNING_IN_THREAD = """
UPDATE chat_executions
   SET status = 'queued', started_at = NULL, updated_at = $1
 WHERE status = 'running' AND updated_at < $2 AND requested_backend = $3 AND thread_id = $4
RETURNING id
"""

_CLAIM_QUEUED = """
UPDATE chat_executions
   SET status = 'running', started_at = $2, updated_at = $2
 WHERE id = $1 AND status = 'queued'
RETURNING id
"""

_BEGIN_ATTEMPT = """
UPDATE chat_executions
   SET attempt = $2,
       draft_token_hash = COALESCE(draft_token_hash, $3),
       draft_text = '',
       draft_seq = 0,
       updated_at = $4
 WHERE id = $1 AND status = 'running' AND attempt <= $2
RETURNING id
"""

_CHECKPOINT_RUNNING = """
UPDATE chat_executions
   SET draft_text = $3, draft_seq = $4, updated_at = $5
 WHERE id = $1 AND status = 'running' AND attempt = $2 AND draft_seq < $4
RETURNING id
"""

_COMPLETE_RUNNING = """
UPDATE chat_executions
   SET status = 'completed',
       assistant_message_id = $2,
       model_used = $3,
       cost_usd = $4,
       num_turns = $5,
       stop_reason = $6,
       usage = $7,
       completed_at = $8,
       updated_at = $8
 WHERE id = $1 AND status = 'running'
   AND (EXISTS (
       SELECT 1 FROM agent_run_observations observation
        WHERE observation.execution_id = chat_executions.id
          AND observation.user_id = chat_executions.user_id
          AND observation.status = 'succeeded'
   ) OR NOT EXISTS (
       SELECT 1 FROM agent_run_observations observation
        WHERE observation.execution_id = chat_executions.id
   ))
RETURNING id
"""

_RECORD_CANCELED_OUTCOME = """
UPDATE chat_executions
   SET status = 'canceled',
       assistant_message_id = $2,
       model_used = $3,
       cost_usd = $4,
       num_turns = $5,
       stop_reason = $6,
       usage = $7,
       completed_at = COALESCE(completed_at, $8),
       updated_at = $8
 WHERE id = $1 AND assistant_message_id IS NULL AND (
       status = 'canceled'
       OR (status = 'running' AND EXISTS (
           SELECT 1 FROM agent_run_observations observation
            WHERE observation.execution_id = chat_executions.id
              AND observation.user_id = chat_executions.user_id
              AND observation.status = 'cancelled'
       ))
 )
RETURNING id
"""

_FAIL_ACTIVE = """
UPDATE chat_executions
   SET status = 'failed', error = $2, completed_at = $3, updated_at = $3
 WHERE id = $1 AND (
       status = 'queued'
       OR (status = 'running' AND (EXISTS (
           SELECT 1 FROM agent_run_observations observation
            WHERE observation.execution_id = chat_executions.id
              AND observation.user_id = chat_executions.user_id
              AND observation.status = 'failed'
       ) OR NOT EXISTS (
           SELECT 1 FROM agent_run_observations observation
            WHERE observation.execution_id = chat_executions.id
       )))
 )
RETURNING id
"""

_CANCEL_ACTIVE = """
UPDATE chat_executions
   SET status = 'canceled', completed_at = $2, updated_at = $2
 WHERE id = $1 AND (
       status = 'queued'
       OR (status = 'running' AND (EXISTS (
           SELECT 1 FROM agent_run_observations observation
            WHERE observation.execution_id = chat_executions.id
              AND observation.user_id = chat_executions.user_id
              AND observation.status = 'cancelled'
       ) OR NOT EXISTS (
           SELECT 1 FROM agent_run_observations observation
            WHERE observation.execution_id = chat_executions.id
       )))
 )
RETURNING id
"""


@dataclass(frozen=True)
class ChatExecutionSpend:
    """턴 하나가 실제로 쓴 지출과 모델이 응답을 멈춘 이유다."""

    model_used: str
    cost_usd: float | None
    num_turns: int | None
    stop_reason: str
    usage: dict[str, Any]


class ChatExecutionLedger:
    """chat 실행 행을 조건부 갱신으로만 전진시키고 조건이 막으면 거짓을 낸다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def find_by_id(self, execution_id: str) -> SqlRow | None:
        """실행 행 하나를 있는 그대로 읽는다."""
        rows = await self._sql.fetch(_SELECT, execution_id)
        return rows[0] if rows else None

    async def list_active(self) -> list[SqlRow]:
        """이 축이 접수한 것 가운데 아직 끝나지 않은 실행을 접수 순서대로 낸다."""
        return await self._sql.fetch(_SELECT_ACTIVE, AGENT_BACKEND)

    async def next_queued_in_thread(self, thread_id: str) -> str | None:
        """이 스레드에서 이 축이 맡은 다음 대기 실행 하나를 내고 없으면 아무것도 내지 않는다."""
        rows = await self._sql.fetch(_SELECT_NEXT_QUEUED_IN_THREAD, thread_id, AGENT_BACKEND)
        return str(rows[0]["id"]) if rows else None

    async def recover_stale_running(
        self, idle_before: datetime, now: datetime, thread_id: str | None = None
    ) -> int:
        """이 축이 접수한 것 가운데 갱신이 끊긴 running 실행을 대기 자리로 되돌리고 개수를 낸다."""
        if thread_id is None:
            rows = await self._sql.fetch(_RECOVER_STALE_RUNNING, now, idle_before, AGENT_BACKEND)
        else:
            rows = await self._sql.fetch(
                _RECOVER_STALE_RUNNING_IN_THREAD, now, idle_before, AGENT_BACKEND, thread_id
            )
        return len(rows)

    async def claim_queued(self, execution_id: str, now: datetime) -> str:
        """대기 실행 하나를 running 자리로 옮기고 집었는지 아닌지를 낸다."""
        try:
            rows = await self._sql.fetch(_CLAIM_QUEUED, execution_id, now)
        except UniqueViolation:
            # 스레드가 잠긴 것은 이 실행의 결함이 아니므로 예외가 아니라 결과로 돌려준다.
            return THREAD_BUSY
        return CLAIMED if rows else STALE

    async def begin_attempt(
        self, execution_id: str, attempt: int, draft_token_hash: str, now: datetime
    ) -> bool:
        """시도 회차를 올리고 진행 표시를 비운다."""
        rows = await self._sql.fetch(_BEGIN_ATTEMPT, execution_id, attempt, draft_token_hash, now)
        return len(rows) == 1

    async def checkpoint_running(
        self, execution_id: str, attempt: int, draft_text: str, draft_seq: int, now: datetime
    ) -> bool:
        """진행 중인 답변 스냅샷을 앞선 순번일 때만 덮어쓴다."""
        rows = await self._sql.fetch(_CHECKPOINT_RUNNING, execution_id, attempt, draft_text, draft_seq, now)
        return len(rows) == 1

    async def complete_running(
        self, execution_id: str, assistant_message_id: str, spend: ChatExecutionSpend, now: datetime
    ) -> bool:
        """running 실행을 산출물과 지출과 함께 완료로 닫는다."""
        rows = await self._sql.fetch(
            _COMPLETE_RUNNING, execution_id, assistant_message_id, *_spend_args(spend), now
        )
        return len(rows) == 1

    async def record_canceled_outcome(
        self, execution_id: str, assistant_message_id: str, spend: ChatExecutionSpend, now: datetime
    ) -> bool:
        """취소된 실행의 아직 비어 있는 산출물 자리에만 한 번 적는다."""
        rows = await self._sql.fetch(
            _RECORD_CANCELED_OUTCOME, execution_id, assistant_message_id, *_spend_args(spend), now
        )
        return len(rows) == 1

    async def fail_active(self, execution_id: str, error: str, now: datetime) -> bool:
        """아직 살아 있는 실행을 사유와 함께 실패로 닫는다."""
        rows = await self._sql.fetch(_FAIL_ACTIVE, execution_id, error, now)
        return len(rows) == 1

    async def cancel_active(self, execution_id: str, now: datetime) -> bool:
        """아직 살아 있는 실행을 취소로 닫는다."""
        rows = await self._sql.fetch(_CANCEL_ACTIVE, execution_id, now)
        return len(rows) == 1


def _spend_args(spend: ChatExecutionSpend) -> tuple[Any, ...]:
    return (spend.model_used, spend.cost_usd, spend.num_turns, spend.stop_reason, spend.usage)
