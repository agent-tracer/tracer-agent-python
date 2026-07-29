"""잡 실행 행이 조건부 갱신으로만 전진하고 같은 식별자나 같은 멱등키는 새로 세우지 않는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.workflows.jobs_ledger import GraphJobLedger

NOW = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 28, 0, 5, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


async def test_새_실행은_대기_행으로_세워진다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)

    assert await ledger.claim("e1", "u1", "title-suggestion", "idem-1", "task-1", 2.0, NOW) is True

    row = store.rows("graph_job_executions")[0]
    assert row["status"] == "queued"
    assert row["budget_usd"] == 2.0
    assert row["idempotency_key"] == "idem-1"
    assert row["task_id"] == "task-1"


async def test_같은_실행_식별자를_다시_보내도_새_행이_생기지_않는다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)
    await ledger.claim("e1", "u1", "title-suggestion", "idem-1", None, 2.0, NOW)

    assert await ledger.claim("e1", "u1", "title-suggestion", "idem-1", None, 2.0, NOW) is False

    assert len(store.rows("graph_job_executions")) == 1


async def test_같은_멱등키가_다른_실행_식별자로_와도_거짓을_낸다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)
    await ledger.claim("e1", "u1", "title-suggestion", "idem-1", None, 2.0, NOW)

    assert await ledger.claim("e2", "u1", "title-suggestion", "idem-1", None, 2.0, NOW) is False

    assert len(store.rows("graph_job_executions")) == 1


async def test_멱등키가_없으면_서로_다른_실행이_각자_행을_세운다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)

    assert await ledger.claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW) is True
    assert await ledger.claim("e2", "u1", "title-suggestion", None, None, 2.0, NOW) is True

    assert len(store.rows("graph_job_executions")) == 2


async def test_실행_중_표시는_대기_행에서만_옮겨간다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)
    await ledger.claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW)

    assert await ledger.mark_running("e1", LATER) is True

    row = store.rows("graph_job_executions")[0]
    assert row["status"] == "running"
    assert row["started_at"] == LATER


async def test_이미_실행_중인_행은_다시_옮기지_않는다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)
    await ledger.claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW)
    await ledger.mark_running("e1", NOW)

    assert await ledger.mark_running("e1", LATER) is False


async def test_종료는_지출과_상태를_함께_남긴다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)
    await ledger.claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW)
    await ledger.mark_running("e1", NOW)

    assert await ledger.settle("e1", "completed", 0.42, None, LATER) is True

    row = store.rows("graph_job_executions")[0]
    assert row["status"] == "completed"
    assert row["cost_usd"] == 0.42
    assert row["completed_at"] == LATER


async def test_종료는_구조화_결과도_함께_남긴다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)
    await ledger.claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW)
    await ledger.mark_running("e1", NOW)

    assert await ledger.settle("e1", "completed", 0.42, None, LATER, {"suggestions": []}) is True

    row = store.rows("graph_job_executions")[0]
    assert row["result"] == {"suggestions": []}


async def test_이미_끝난_행은_다시_닫히지_않는다(store: SqliteLedgerSql) -> None:
    ledger = GraphJobLedger(store)
    await ledger.claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW)
    await ledger.settle("e1", "completed", 0.1, None, NOW)

    assert await ledger.settle("e1", "failed", None, "boom", LATER) is False

    row = store.rows("graph_job_executions")[0]
    assert row["status"] == "completed"
