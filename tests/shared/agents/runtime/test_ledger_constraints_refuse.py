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

    @pytest.mark.parametrize(
        ("table", "owner"),
        [("chat_execution_steps", "execution_id"), ("ai_job_steps", "job_id")],
    )
    def test_같은_시도의_다음_순번을_받는다(self, raw: Any, table: str, owner: str) -> None:
        _step(raw, table, owner, "s1", 1, 1)

        _step(raw, table, owner, "s2", 1, 2)

    @pytest.mark.parametrize(
        ("table", "owner"),
        [("chat_execution_steps", "execution_id"), ("ai_job_steps", "job_id")],
    )
    def test_다른_실행은_같은_순번을_따로_센다(self, raw: Any, table: str, owner: str) -> None:
        # 색인에서 주인이 빠지면 한 실행의 순번이 다른 실행의 순번을 막는다.
        _step(raw, table, owner, "s1", 1, 1)

        raw.execute(
            f"INSERT INTO {table} (id, {owner}, user_id, attempt, seq, role, content, created_at)"
            " VALUES ('s2', 'x2', 'u1', 1, 1, 'assistant', '말', ?)",
            (NOW,),
        )


def _suggestion(raw: Any, suggestion_id: str, status: str, task_id: str = "t1") -> None:
    raw.execute(
        "INSERT INTO task_cleanup_suggestions"
        " (id, user_id, job_id, task_id, kind, rationale, status, created_at)"
        " VALUES (?, 'u1', 'job-1', ?, 'archive', '사건이 오래 없다', ?, ?)",
        (suggestion_id, task_id, status, NOW),
    )


class Test한_태스크와_한_종류에_대기_중인_제안은_하나:
    def test_같은_태스크와_종류의_두_번째_대기_행을_거절한다(self, raw: Any) -> None:
        # cleanup_pending_task_kind_unique 가 다시 스캔해도 대기 행의 수를 늘리지 않는다.
        _suggestion(raw, "s1", "pending")

        with pytest.raises(sqlite3.IntegrityError) as refused:
            _suggestion(raw, "s2", "pending")

        assert "task_cleanup_suggestions.kind" in str(refused.value)

    def test_앞의_행이_해소되었으면_새_대기_행을_만든다(self, raw: Any) -> None:
        _suggestion(raw, "s1", "dismissed")

        _suggestion(raw, "s2", "pending")

    def test_태스크가_다르면_대기_행이_여럿_남는다(self, raw: Any) -> None:
        _suggestion(raw, "s1", "pending")

        _suggestion(raw, "s2", "pending", task_id="t2")


def _outbox(raw: Any, row_id: str, target: str, backend: str = "python") -> None:
    raw.execute(
        "INSERT INTO search_outbox (id, backend, user_id, target, target_id, attempts, created_at)"
        " VALUES (?, ?, 'u1', ?, 'recipe-1', 0, ?)",
        (row_id, backend, target, NOW),
    )


class Test에이전트_원장이_소유한_색인_대상은_레시피_하나:
    def test_레시피가_아닌_대상의_적재를_거절한다(self, raw: Any) -> None:
        # search_outbox_target_check 가 아무도 배출하지 않는 대상을 이 표에 들이지 않는다.
        with pytest.raises(sqlite3.IntegrityError) as refused:
            _outbox(raw, "o1", "task")

        assert "target = 'recipe'" in str(refused.value)

    def test_레시피_대상의_적재는_받는다(self, raw: Any) -> None:
        _outbox(raw, "o2", "recipe")


def _application(raw: Any, row_id: str, backend: str, task_id: str = "t1") -> None:
    raw.execute(
        "INSERT INTO recipe_applications"
        " (id, backend, user_id, recipe_id, task_id, injected_via, created_at)"
        " VALUES (?, ?, 'u1', 'recipe-1', ?, 'pull', ?)",
        (row_id, backend, task_id, NOW),
    )


class Test배경_작업의_행은_축을_갖는다:
    def test_계약이_적지_않은_축의_적용_이력을_거절한다(self, raw: Any) -> None:
        with pytest.raises(sqlite3.IntegrityError) as refused:
            _application(raw, "a1", "rust")

        assert "recipe_applications_backend_check" in str(refused.value)

    def test_계약이_적지_않은_축의_적재를_거절한다(self, raw: Any) -> None:
        with pytest.raises(sqlite3.IntegrityError) as refused:
            _outbox(raw, "o3", "recipe", backend="rust")

        assert "search_outbox_backend_check" in str(refused.value)

    def test_두_축이_같은_식별자의_적용_이력을_함께_갖는다(self, raw: Any) -> None:
        # 사건이 식별자를 싣고 오므로 기본 키가 축을 앞에 두지 않으면 뒤의 축이 앞의 행을 덮는다.
        _application(raw, "a1", "ts")

        _application(raw, "a1", "python")

        assert len(raw.execute("SELECT id FROM recipe_applications").fetchall()) == 2

    def test_같은_축의_같은_식별자를_두_번_적으면_거절한다(self, raw: Any) -> None:
        _application(raw, "a1", "python")

        with pytest.raises(sqlite3.IntegrityError) as refused:
            _application(raw, "a1", "python", task_id="t2")

        assert "recipe_applications.id" in str(refused.value)
