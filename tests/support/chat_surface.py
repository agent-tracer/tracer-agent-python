"""대화 창구 테스트가 함께 쓰는 메모리 원장 대역과 원장 심기다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql

NOW = datetime(2026, 7, 30, tzinfo=UTC)

# 실행 봉투가 실어 보내는 draft 자격의 평문과 원장이 드는 그 지문이다.
DRAFT_TOKEN = "grant"
DRAFT_TOKEN_HASH = "3492ad65d05a973fef8c825521eeb41ae64625a672a8aeeeabc696e16d62a020"


class SingleSql:
    """테스트 하나가 쓰는 메모리 원장을 창구에 그대로 빌려 준다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """빌릴 때마다 같은 메모리 원장을 낸다."""
        return self._lend()

    @asynccontextmanager
    async def _lend(self) -> AsyncIterator[LedgerSql]:
        yield self._store


class RecordingDispatch:
    """워크플로 기동과 중단 요청이 갔는지만 기록한다."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.canceled: list[str] = []

    async def start(self, execution_id: str, thread_id: str) -> None:
        """기동 요청을 받은 실행을 적고 이 대역은 실행을 수행하지 않는다."""
        self.started.append((execution_id, thread_id))

    async def cancel(self, execution_id: str) -> None:
        """중단 요청을 받은 실행을 적는다."""
        self.canceled.append(execution_id)


class SilentWatch:
    """알림 신호가 오지 않는 구독 창구를 대신한다."""

    def __init__(self) -> None:
        self.notified: list[str] = []

    def subscribe(self, _execution_id: str, _listener: Any) -> Any:
        """아무 신호도 주지 않고 그만 듣는 방법만 낸다."""
        return lambda: None

    def notify(self, execution_id: str) -> None:
        """이 프로세스가 스스로 알린 실행을 기억한다."""
        self.notified.append(execution_id)

    async def close(self) -> None:
        """닫을 것이 없다."""


class RecordingUpdates:
    """알림 신호가 어느 실행으로 갔는지 적는다."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, key: str, _payload: dict[str, str]) -> bool:
        """갱신 사실 하나를 적고 보냈다고 낸다."""
        self.published.append(key)
        return True

    async def close(self) -> None:
        """닫을 것이 없다."""


class RecordingExecutor:
    """승인된 도구 호출을 부르는 대신 그 이름과 인자를 적는다."""

    def __init__(self, sentence: str = "Archived task task-1.") -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._sentence = sentence

    async def execute(self, user_id: str, tool_name: str, args: dict[str, Any]) -> str:
        """호출을 적고 대화에 남길 문장을 낸다."""
        self.calls.append((user_id, tool_name, args))
        return self._sentence


def seed_thread(store: SqliteLedgerSql, thread_id: str = "t1", user_id: str = "local", **extra: Any) -> None:
    """조회가 읽을 스레드 한 행을 기록한다."""
    store.seed(
        "chat_threads",
        [
            {
                "id": thread_id,
                "user_id": user_id,
                "title": extra.get("title", "첫 대화"),
                "summary": extra.get("summary"),
                "backend": extra.get("backend"),
                "created_at": NOW,
                "updated_at": extra.get("updated_at", NOW),
            }
        ],
    )


def seed_message(
    store: SqliteLedgerSql, message_id: str, role: str, content: str, offset: int = 0, **extra: Any
) -> None:
    """조회가 읽을 대화 한 줄을 기록한다."""
    store.seed(
        "chat_messages",
        [
            {
                "id": message_id,
                "thread_id": extra.get("thread_id", "t1"),
                "role": role,
                "content": content,
                "tool_calls": extra.get("tool_calls"),
                "tool_call_id": extra.get("tool_call_id"),
                "created_at": NOW + timedelta(seconds=offset),
            }
        ],
    )


def seed_execution(
    store: SqliteLedgerSql, execution_id: str = "e1", replay_anchor_message_id: str = "m1", **extra: Any
) -> None:
    """조회가 읽을 실행 한 행을 기록한다."""
    store.seed(
        "chat_executions",
        [
            {
                "id": execution_id,
                "user_id": extra.get("user_id", "local"),
                "thread_id": extra.get("thread_id", "t1"),
                "replay_anchor_message_id": replay_anchor_message_id,
                "client_request_id": execution_id,
                "input_hash": "hash",
                "status": extra.get("status", "running"),
                "language": "ko",
                "draft_text": extra.get("draft_text", ""),
                "draft_seq": extra.get("draft_seq", 0),
                "attempt": extra.get("attempt", 1),
                "draft_token_hash": extra.get("draft_token_hash"),
                "usage": {},
                "created_at": NOW + timedelta(seconds=extra.get("offset", 0)),
                "updated_at": extra.get("updated_at", NOW),
            }
        ],
    )


def seed_pending_tool(
    store: SqliteLedgerSql, confirmation_id: str = "c1", status: str = "pending", **extra: Any
) -> None:
    """조회가 읽을 확인 대기 행 하나를 기록한다."""
    store.seed(
        "chat_pending_tools",
        [
            {
                "id": confirmation_id,
                "thread_id": extra.get("thread_id", "t1"),
                "tool_name": extra.get("tool_name", "propose_task_write"),
                "args": extra.get("args", {"action": "archive", "taskId": "task-1"}),
                "status": status,
                "created_at": NOW,
            }
        ],
    )


def seed_memory(store: SqliteLedgerSql, key: str = "lang", content: str = "한국어를 쓴다") -> None:
    """조회가 읽을 사용자 장기기억 한 줄을 기록한다."""
    store.seed(
        "chat_user_memories",
        [
            {
                "id": f"f-{key}",
                "user_id": "local",
                "key": key,
                "content": content,
                "created_at": NOW,
                "updated_at": NOW,
            }
        ],
    )


def seed_step(store: SqliteLedgerSql, step_id: str, attempt: int, seq: int, **extra: Any) -> None:
    """조회가 읽을 궤적 한 줄을 기록한다."""
    row: dict[str, Any] = {
        "id": step_id,
        "execution_id": extra.get("execution_id", "e1"),
        "user_id": "local",
        "attempt": attempt,
        "seq": seq,
        "role": extra.get("role", "assistant"),
        "content": extra.get("content", "말"),
        "truncated": False,
        "created_at": NOW + timedelta(seconds=seq),
    }
    row.update({key: value for key, value in extra.items() if key.islower() and key in _STEP_COLUMNS})
    store.seed("chat_execution_steps", [row])


_STEP_COLUMNS = frozenset(
    {
        "tool_calls",
        "tool_name",
        "tool_call_id",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "stop_reason",
        "node_name",
        "event_kind",
        "duration_ms",
    }
)
