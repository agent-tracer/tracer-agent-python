"""접수가 원장에 세우는 행의 조회와 삽입을 문장 한 벌로 소유한다."""

from __future__ import annotations

from datetime import datetime

from ...runtime.ledger import LedgerSql, SqlRow

QUEUED = "queued"

_SELECT_THREAD_OWNER = "SELECT user_id FROM agent_chat_thread_view WHERE id = $1"

_SELECT_BY_IDEMPOTENCY = """
SELECT * FROM chat_executions
 WHERE user_id = $1 AND thread_id = $2 AND client_request_id = $3
"""

_SELECT_MESSAGE = "SELECT * FROM chat_messages WHERE id = $1"

# 아직 끝나지 않은 턴을 다른 백엔드가 쥐고 있으면 두 워크플로가 같은 스레드를 나눠 집는다.
_SELECT_FOREIGN_ACTIVE = """
SELECT id FROM chat_executions
 WHERE thread_id = $1
   AND status IN ('queued', 'running')
   AND (requested_backend IS NULL OR requested_backend <> $2)
 LIMIT 1
"""

_INSERT_USER_MESSAGE = """
INSERT INTO chat_messages (id, thread_id, role, content, tool_calls, tool_call_id, created_at)
VALUES ($1, $2, 'user', $3, NULL, NULL, $4)
RETURNING *
"""

_INSERT_QUEUED_EXECUTION = """
INSERT INTO chat_executions (
    id, user_id, thread_id, user_message_id, client_request_id, input_hash, status,
    requested_backend, model, language, draft_text, draft_seq, attempt, usage,
    created_at, updated_at
)
VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7, $8, $9, '', 0, 0, $10, $11, $11)
RETURNING *
"""


class ChatIntakeLedger:
    """소유권 확인과 멱등 조회와 접수 삽입에 쓰는 원장 문장을 소유한다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def thread_owner(self, thread_id: str) -> str | None:
        """스레드의 주인을 계약 뷰로만 읽고 없으면 아무것도 내지 않는다."""
        rows = await self._sql.fetch(_SELECT_THREAD_OWNER, thread_id)
        return str(rows[0]["user_id"]) if rows else None

    async def find_by_idempotency(
        self, user_id: str, thread_id: str, client_request_id: str
    ) -> SqlRow | None:
        """같은 요청 식별자로 이미 선 실행이 있으면 그 행을 낸다."""
        rows = await self._sql.fetch(_SELECT_BY_IDEMPOTENCY, user_id, thread_id, client_request_id)
        return rows[0] if rows else None

    async def has_active_on_other_backend(self, thread_id: str, backend: str) -> bool:
        """이 스레드에 다른 백엔드가 쥔 아직 끝나지 않은 턴이 있는지 낸다."""
        rows = await self._sql.fetch(_SELECT_FOREIGN_ACTIVE, thread_id, backend)
        return len(rows) > 0

    async def find_message(self, message_id: str) -> SqlRow | None:
        """실행이 인용하는 사용자 메시지 행을 읽는다."""
        rows = await self._sql.fetch(_SELECT_MESSAGE, message_id)
        return rows[0] if rows else None

    async def insert_user_message(
        self, message_id: str, thread_id: str, content: str, now: datetime
    ) -> SqlRow:
        """이번 턴의 사용자 발화를 새 행으로 적는다."""
        rows = await self._sql.fetch(_INSERT_USER_MESSAGE, message_id, thread_id, content, now)
        return rows[0]

    async def insert_queued_execution(
        self,
        execution_id: str,
        user_id: str,
        thread_id: str,
        user_message_id: str,
        client_request_id: str,
        input_hash: str,
        requested_backend: str | None,
        model: str | None,
        language: str | None,
        now: datetime,
    ) -> SqlRow:
        """이번 턴을 태울 실행을 대기 상태로 새로 세운다."""
        rows = await self._sql.fetch(
            _INSERT_QUEUED_EXECUTION,
            execution_id,
            user_id,
            thread_id,
            user_message_id,
            client_request_id,
            input_hash,
            requested_backend,
            model,
            language,
            {},
            now,
        )
        return rows[0]
