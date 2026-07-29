"""Graph 접수구가 title·recipe·cleanup 잡의 실행 원장 행을 조건부 갱신으로 소유한다."""

from __future__ import annotations

from datetime import datetime

from ..agents.runtime.ledger import LedgerSql, UniqueViolation

_CLAIM = """
INSERT INTO graph_job_executions (
    id, user_id, kind, idempotency_key, task_id, status, budget_usd, created_at, updated_at
)
VALUES ($1, $2, $3, $4, $5, 'queued', $6, $7, $7)
ON CONFLICT (id) DO NOTHING
RETURNING id
"""

_MARK_RUNNING = """
UPDATE graph_job_executions
   SET status = 'running', started_at = $2, updated_at = $2
 WHERE id = $1 AND status = 'queued'
RETURNING id
"""

_SETTLE = """
UPDATE graph_job_executions
   SET status = $2, cost_usd = $3, error = $4, result = $5, completed_at = $6, updated_at = $6
 WHERE id = $1 AND status IN ('queued', 'running')
RETURNING id
"""

_FIND_KIND = "SELECT kind FROM graph_job_executions WHERE id = $1"


class GraphJobLedger:
    """잡 실행 행을 조건부 갱신으로만 전진시키고 조건이 막으면 거짓을 낸다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def claim(
        self,
        execution_id: str,
        user_id: str,
        kind: str,
        idempotency_key: str | None,
        task_id: str | None,
        budget_usd: float,
        now: datetime,
    ) -> bool:
        """이 실행 식별자나 이 멱등키의 행이 이미 있으면 새로 세우지 않고 거짓을 낸다."""
        try:
            rows = await self._sql.fetch(
                _CLAIM, execution_id, user_id, kind, idempotency_key, task_id, budget_usd, now
            )
        except UniqueViolation:
            # 다른 실행 식별자가 같은 (kind, idempotency_key)를 이미 썼다는 뜻이다.
            return False
        return len(rows) == 1

    async def find_kind(self, execution_id: str) -> str | None:
        """취소가 워크플로 식별자를 다시 만들 때 쓸 이 실행의 잡 종류를 읽는다."""
        rows = await self._sql.fetch(_FIND_KIND, execution_id)
        return rows[0]["kind"] if rows else None

    async def mark_running(self, execution_id: str, now: datetime) -> bool:
        """접수된 잡을 실행 중 표시로 옮긴다."""
        rows = await self._sql.fetch(_MARK_RUNNING, execution_id, now)
        return len(rows) == 1

    async def settle(
        self,
        execution_id: str,
        status: str,
        cost_usd: float | None,
        error: str | None,
        now: datetime,
        result: dict[str, object] | None = None,
    ) -> bool:
        """살아 있는 잡을 종료 상태와 실제 지출과 구조화 결과로 닫는다."""
        rows = await self._sql.fetch(_SETTLE, execution_id, status, cost_usd, error, result, now)
        return len(rows) == 1
