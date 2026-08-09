"""원장의 제약이 실제로 거절하는지, 그리고 거절한 것이 그 제약인지 검증한다."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest

from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql

NOW = "2026-08-09T00:00:00Z"


@pytest.fixture
def raw() -> Iterator[Any]:
    store = SqliteLedgerSql()
    yield store.raw
    store.close()


def _thread(raw: Any, thread_id: str, summary: str | None, through: str | None) -> None:
    raw.execute(
        "INSERT INTO chat_threads"
        " (id, user_id, title, summary, summary_through_message_id, backend, created_at, updated_at)"
        " VALUES (?, 'u1', '대화', ?, ?, NULL, ?, ?)",
        (thread_id, summary, through, NOW, NOW),
    )


def _execution(raw: Any, execution_id: str, status: str, request_id: str) -> None:
    raw.execute(
        "INSERT INTO chat_executions"
        " (id, user_id, thread_id, replay_anchor_message_id, client_request_id, input_hash, status,"
        "  created_at, updated_at)"
        " VALUES (?, 'u1', 't1', 'm1', ?, 'h1', ?, ?, ?)",
        (execution_id, request_id, status, NOW, NOW),
    )


class Test요약과_지점의_짝:
    def test_요약만_적으면_원장이_거절한다(self, raw: Any) -> None:
        with pytest.raises(sqlite3.IntegrityError) as refused:
            _thread(raw, "t1", "있음", None)

        assert "summary_through_message_id" in str(refused.value)

    def test_지점만_적어도_거절한다(self, raw: Any) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            _thread(raw, "t1", None, "m9")

    def test_함께_있거나_함께_없으면_받는다(self, raw: Any) -> None:
        _thread(raw, "t1", None, None)
        _thread(raw, "t2", "있음", "m9")


class Test스레드에_running은_하나:
    def test_같은_스레드의_두_번째_running을_거절한다(self, raw: Any) -> None:
        _thread(raw, "t1", None, None)
        _execution(raw, "e1", "running", "r1")

        with pytest.raises(sqlite3.IntegrityError) as refused:
            _execution(raw, "e2", "running", "r2")

        # 멱등 색인이 먼저 걸리면 보려던 제약에 닿지 못하므로 거절한 색인을 확인한다.
        assert "chat_executions.thread_id" in str(refused.value)

    def test_끝난_턴이_있는_스레드는_다음_running을_받는다(self, raw: Any) -> None:
        # 부분 색인의 조건이 빠지면 이 자리가 거절로 바뀌어 스레드가 다음 턴을 열지 못한다.
        _thread(raw, "t1", None, None)
        _execution(raw, "e1", "completed", "r1")

        _execution(raw, "e2", "running", "r2")


class Test사용자_사실:
    def test_같은_열쇠를_두_번_적으면_거절한다(self, raw: Any) -> None:
        raw.execute(
            "INSERT INTO chat_user_memories (id, user_id, key, content, created_at, updated_at)"
            " VALUES ('m1', 'u1', 'k', 'v', ?, ?)",
            (NOW, NOW),
        )

        with pytest.raises(sqlite3.IntegrityError) as refused:
            raw.execute(
                "INSERT INTO chat_user_memories (id, user_id, key, content, created_at, updated_at)"
                " VALUES ('m2', 'u1', 'k', 'v', ?, ?)",
                (NOW, NOW),
            )

        assert "chat_user_memories.user_id" in str(refused.value)
