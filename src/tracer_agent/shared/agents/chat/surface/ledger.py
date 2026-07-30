"""대화 창구가 원장에 묻는 조회와 쓰기를 문장 한 벌씩으로 소유한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...runtime.ledger import LedgerSql, SqlRow

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"

_SELECT_THREAD = "SELECT * FROM chat_threads WHERE id = $1"

_SELECT_THREADS_BY_USER = """
SELECT * FROM chat_threads
 WHERE user_id = $1
 ORDER BY updated_at DESC
"""

_INSERT_THREAD = """
INSERT INTO chat_threads (id, user_id, title, summary, backend, created_at, updated_at)
VALUES ($1, $2, $3, NULL, NULL, $4, $4)
RETURNING *
"""

_RENAME_THREAD = """
UPDATE chat_threads SET title = $2, updated_at = $3
 WHERE id = $1
RETURNING *
"""

_SELECT_MESSAGES = """
SELECT * FROM chat_messages
 WHERE thread_id = $1
 ORDER BY created_at ASC, id ASC
"""

_INSERT_TOOL_MESSAGE = """
INSERT INTO chat_messages (id, thread_id, role, content, tool_calls, tool_call_id, created_at)
VALUES ($1, $2, 'tool', $3, NULL, $4, $5)
"""

_SELECT_EXECUTIONS = """
SELECT * FROM chat_executions
 WHERE thread_id = $1
 ORDER BY created_at DESC
"""

_SELECT_EXECUTION = "SELECT * FROM chat_executions WHERE id = $1"

_SELECT_LATEST_ACTIVE = """
SELECT id FROM chat_executions
 WHERE thread_id = $1 AND status IN ('queued', 'running')
 ORDER BY created_at DESC
 LIMIT 1
"""

_SELECT_STEPS = """
SELECT * FROM chat_execution_steps
 WHERE execution_id = $1 AND user_id = $2
 ORDER BY attempt ASC, seq ASC
"""

_SELECT_PENDING_TOOLS = """
SELECT * FROM chat_pending_tools
 WHERE thread_id = $1
 ORDER BY created_at ASC
"""

_SELECT_PENDING_TOOL = "SELECT * FROM chat_pending_tools WHERE id = $1"

_INSERT_PENDING_TOOL = """
INSERT INTO chat_pending_tools (id, thread_id, message_id, tool_name, args, status, created_at)
VALUES ($1, $2, NULL, $3, $4, 'pending', $5)
RETURNING *
"""

_RESOLVE_PENDING_TOOL = """
UPDATE chat_pending_tools SET status = $2, resolved_at = $3
 WHERE id = $1 AND status = 'pending'
RETURNING *
"""

_SELECT_MEMORIES = """
SELECT * FROM chat_user_memories
 WHERE user_id = $1
 ORDER BY updated_at DESC
"""

_UPSERT_MEMORY = """
INSERT INTO chat_user_memories (id, user_id, key, content, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $5)
ON CONFLICT (user_id, key) DO UPDATE
   SET content = EXCLUDED.content, updated_at = EXCLUDED.updated_at
"""

_DELETE_STEPS_BY_THREAD = """
DELETE FROM chat_execution_steps
 WHERE execution_id IN (SELECT id FROM chat_executions WHERE thread_id = $1)
"""

_DELETE_PENDING_TOOLS_BY_THREAD = "DELETE FROM chat_pending_tools WHERE thread_id = $1"
_DELETE_MESSAGES_BY_THREAD = "DELETE FROM chat_messages WHERE thread_id = $1"
_DELETE_EXECUTIONS_BY_THREAD = "DELETE FROM chat_executions WHERE thread_id = $1"
_DELETE_THREAD = "DELETE FROM chat_threads WHERE id = $1"


class ChatSurfaceLedger:
    """창구가 대화 원장을 읽고 쓰는 문장을 한자리에 소유한다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def find_thread(self, thread_id: str) -> SqlRow | None:
        """스레드 한 행을 있는 그대로 읽는다."""
        rows = await self._sql.fetch(_SELECT_THREAD, thread_id)
        return rows[0] if rows else None

    async def list_threads(self, user_id: str) -> list[SqlRow]:
        """이 사용자의 스레드를 최근 갱신순으로 낸다."""
        return await self._sql.fetch(_SELECT_THREADS_BY_USER, user_id)

    async def insert_thread(self, thread_id: str, user_id: str, title: str, now: datetime) -> SqlRow:
        """새 스레드 한 행을 세운다."""
        rows = await self._sql.fetch(_INSERT_THREAD, thread_id, user_id, title, now)
        return rows[0]

    async def rename_thread(self, thread_id: str, title: str, now: datetime) -> SqlRow:
        """스레드의 제목을 고친 행을 낸다."""
        rows = await self._sql.fetch(_RENAME_THREAD, thread_id, title, now)
        return rows[0]

    async def delete_thread(self, thread_id: str) -> None:
        """스레드를 그 메시지와 실행과 궤적과 대기 도구까지 지운다."""
        async with self._sql.transaction():
            await self._sql.fetch(_DELETE_STEPS_BY_THREAD, thread_id)
            await self._sql.fetch(_DELETE_PENDING_TOOLS_BY_THREAD, thread_id)
            await self._sql.fetch(_DELETE_MESSAGES_BY_THREAD, thread_id)
            await self._sql.fetch(_DELETE_EXECUTIONS_BY_THREAD, thread_id)
            await self._sql.fetch(_DELETE_THREAD, thread_id)

    async def list_messages(self, thread_id: str) -> list[SqlRow]:
        """스레드의 메시지를 쌓인 순서대로 낸다."""
        return await self._sql.fetch(_SELECT_MESSAGES, thread_id)

    async def insert_tool_message(
        self, message_id: str, thread_id: str, content: str, tool_call_id: str, now: datetime
    ) -> None:
        """도구 결과 한 줄을 대화에 남긴다."""
        await self._sql.fetch(_INSERT_TOOL_MESSAGE, message_id, thread_id, content, tool_call_id, now)

    async def list_executions(self, thread_id: str) -> list[SqlRow]:
        """스레드의 실행을 최근순으로 낸다."""
        return await self._sql.fetch(_SELECT_EXECUTIONS, thread_id)

    async def find_execution(self, execution_id: str) -> SqlRow | None:
        """실행 한 행을 있는 그대로 읽는다."""
        rows = await self._sql.fetch(_SELECT_EXECUTION, execution_id)
        return rows[0] if rows else None

    async def latest_active_execution(self, thread_id: str) -> str | None:
        """이 스레드에서 아직 열려 있는 가장 최근 실행의 식별자를 낸다."""
        rows = await self._sql.fetch(_SELECT_LATEST_ACTIVE, thread_id)
        return str(rows[0]["id"]) if rows else None

    async def list_steps(self, execution_id: str, user_id: str) -> list[SqlRow]:
        """실행 하나의 궤적을 시도와 순번의 오름차순으로 낸다."""
        return await self._sql.fetch(_SELECT_STEPS, execution_id, user_id)

    async def list_pending_tools(self, thread_id: str) -> list[SqlRow]:
        """스레드의 확인 대기 행을 쌓인 순서대로 낸다."""
        return await self._sql.fetch(_SELECT_PENDING_TOOLS, thread_id)

    async def find_pending_tool(self, confirmation_id: str) -> SqlRow | None:
        """확인 대기 행 하나를 있는 그대로 읽는다."""
        rows = await self._sql.fetch(_SELECT_PENDING_TOOL, confirmation_id)
        return rows[0] if rows else None

    async def insert_pending_tool(
        self,
        confirmation_id: str,
        thread_id: str,
        tool_name: str,
        args: dict[str, Any],
        now: datetime,
    ) -> SqlRow:
        """쓰기 도구 호출 하나를 확인 대기 행으로 세운다."""
        rows = await self._sql.fetch(_INSERT_PENDING_TOOL, confirmation_id, thread_id, tool_name, args, now)
        return rows[0]

    async def resolve_pending_tool(self, confirmation_id: str, status: str, now: datetime) -> SqlRow | None:
        """대기 중인 확인 하나를 승인이나 거절로 닫고 닫힌 행을 낸다."""
        rows = await self._sql.fetch(_RESOLVE_PENDING_TOOL, confirmation_id, status, now)
        return rows[0] if rows else None

    async def list_memories(self, user_id: str) -> list[SqlRow]:
        """이 사용자의 장기기억을 최근 갱신순으로 낸다."""
        return await self._sql.fetch(_SELECT_MEMORIES, user_id)

    async def upsert_memory(
        self, memory_id: str, user_id: str, key: str, content: str, now: datetime
    ) -> None:
        """사실 하나를 같은 키 자리에 덮어 적는다."""
        await self._sql.fetch(_UPSERT_MEMORY, memory_id, user_id, key, content, now)
