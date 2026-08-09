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


def _job(raw: Any, job_id: str, key: str | None) -> None:
    raw.execute(
        "INSERT INTO ai_jobs"
        " (id, user_id, kind, executor, backend, status, attempts, idempotency_key,"
        "  input, created_at, updated_at)"
        " VALUES (?, 'u1', 'title.suggestion', 'temporal', 'python', 'pending', 0, ?, '{}', ?, ?)",
        (job_id, key, NOW, NOW),
    )


class Test잡의_멱등_열쇠:
    def test_같은_열쇠를_두_번_넣으면_거절한다(self, raw: Any) -> None:
        # 접수는 먼저 조회해 답하므로 경합에서 두 조회가 함께 비었을 때 이 색인이 마지막 방어다.
        _job(raw, "j1", "key-1")

        with pytest.raises(sqlite3.IntegrityError) as refused:
            _job(raw, "j2", "key-1")

        assert "ai_jobs.idempotency_key" in str(refused.value)

    def test_열쇠가_없는_접수는_몇_번이든_선다(self, raw: Any) -> None:
        # 원장이 유일 색인에서 NULL 을 서로 다른 값으로 보므로, 부분 조건이 아니라 NULL 이 이것을 정한다.
        _job(raw, "j1", None)
        _job(raw, "j2", None)

        assert len(raw.execute("SELECT id FROM ai_jobs").fetchall()) == 2


class Test한_스레드의_같은_요청:
    def test_같은_요청_식별자로_두_번_접수하면_거절한다(self, raw: Any) -> None:
        _thread(raw, "t1", None, None)
        _execution(raw, "e1", "completed", "r1")

        with pytest.raises(sqlite3.IntegrityError) as refused:
            _execution(raw, "e2", "completed", "r1")

        assert "chat_executions.client_request_id" in str(refused.value)


class Test실행의_축:
    def test_계약이_적지_않은_축은_원장이_거절한다(self, raw: Any) -> None:
        # 접수가 자기 축 상수를 적으므로, 상류가 실어 보낸 값을 옮겨 적는 길이 생기면 여기서 막힌다.
        _thread(raw, "t1", None, None)

        with pytest.raises(sqlite3.IntegrityError) as refused:
            raw.execute(
                "INSERT INTO chat_executions"
                " (id, user_id, thread_id, replay_anchor_message_id, client_request_id, input_hash,"
                "  status, requested_backend, created_at, updated_at)"
                " VALUES ('e1', 'u1', 't1', 'm1', 'r1', 'h1', 'queued', 'rust', ?, ?)",
                (NOW, NOW),
            )

        assert "requested_backend" in str(refused.value)


def _step(raw: Any, table: str, owner: str, step_id: str, attempt: int, seq: int) -> None:
    raw.execute(
        f"INSERT INTO {table} (id, {owner}, user_id, attempt, seq, role, content, created_at)"
        " VALUES (?, 'x1', 'u1', ?, ?, 'assistant', '말', ?)",
        (step_id, attempt, seq, NOW),
    )


class Test단계의_순번:
    @pytest.mark.parametrize(
        ("table", "owner"),
        [("chat_execution_steps", "execution_id"), ("ai_job_steps", "job_id")],
    )
    def test_같은_시도의_같은_순번을_두_번_적으면_거절한다(self, raw: Any, table: str, owner: str) -> None:
        # 재시도가 같은 시도 번호로 단계를 다시 적으면 궤적이 두 벌이 되므로 원장이 막는다.
        _step(raw, table, owner, "s1", 1, 1)

        with pytest.raises(sqlite3.IntegrityError) as refused:
            _step(raw, table, owner, "s2", 1, 1)

        assert f"{table}.seq" in str(refused.value)

    @pytest.mark.parametrize(
        ("table", "owner"),
        [("chat_execution_steps", "execution_id"), ("ai_job_steps", "job_id")],
    )
    def test_다시_태운_시도는_같은_순번을_받는다(self, raw: Any, table: str, owner: str) -> None:
        _step(raw, table, owner, "s1", 1, 1)

        _step(raw, table, owner, "s2", 2, 1)
