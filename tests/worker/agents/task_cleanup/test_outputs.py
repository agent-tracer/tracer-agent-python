"""종결한 정리 스캔의 제안이 자기 원장에 서는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.support.chat_surface import SingleSql
from tests.support.contract import conformance_case
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.outputs import JobOutputTargets
from tracer_agent.worker.agents.task_cleanup.outputs import write_suggestions

_DERIVED = conformance_case("recipe.ledger")["ledgerWrite"]["derived"]["cleanupSuggestion"]

USER = "user-1"
LAST_EVENT_AT = "2026-01-01T00:01:00.000Z"


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


def _targets(store: SqliteLedgerSql, tasks: list[dict[str, Any]] | None = None) -> JobOutputTargets:
    return JobOutputTargets(SingleSql(store), FakeTracerApi(tasks=tasks or []))


async def test_정리_제안을_적고_잡을_함께_적는다(store: SqliteLedgerSql) -> None:
    await write_suggestions(
        _targets(store), USER, "job-2", {"suggestions": [{"taskId": "t1", "rationale": "오래됐다"}]}
    )

    row = store.rows("task_cleanup_suggestions")[0]
    assert row["job_id"] == "job-2"
    assert row["task_id"] == "t1"
    assert row["kind"] == "archive"
    assert row["status"] == _DERIVED["status"]
    assert row["current_value"] is None
    assert row["proposed_value"] is None
    assert row["error"] is None
    assert row["resolved_at"] is None


async def test_관측한_마지막_사건_시각을_추적에_물어_적는다(store: SqliteLedgerSql) -> None:
    tasks = [{"id": "t1", "lastEventAt": LAST_EVENT_AT}]

    await write_suggestions(
        _targets(store, tasks), USER, "job-3", {"suggestions": [{"taskId": "t1", "rationale": "오래됐다"}]}
    )

    row = store.rows("task_cleanup_suggestions")[0]
    assert row["observed_last_event_at"] is not None


async def test_같은_태스크와_종류의_대기_행에_새_근거를_겹쳐_적는다(store: SqliteLedgerSql) -> None:
    await write_suggestions(
        _targets(store), USER, "job-4", {"suggestions": [{"taskId": "t1", "rationale": "처음"}]}
    )
    await write_suggestions(
        _targets(store), USER, "job-5", {"suggestions": [{"taskId": "t1", "rationale": "다시"}]}
    )

    rows = store.rows("task_cleanup_suggestions")
    assert len(rows) == 1
    assert rows[0]["rationale"] == "다시"
    assert rows[0]["job_id"] == "job-5"


async def test_해소된_행이_있으면_새_대기_행을_만든다(store: SqliteLedgerSql) -> None:
    await write_suggestions(
        _targets(store), USER, "job-6", {"suggestions": [{"taskId": "t1", "rationale": "처음"}]}
    )
    store.raw.execute("UPDATE task_cleanup_suggestions SET status = 'dismissed'")

    await write_suggestions(
        _targets(store), USER, "job-7", {"suggestions": [{"taskId": "t1", "rationale": "다시"}]}
    )

    assert len(store.rows("task_cleanup_suggestions")) == 2


async def test_제안이_없으면_원장을_열지_않는다(store: SqliteLedgerSql) -> None:
    await write_suggestions(_targets(store), USER, "job-8", {"suggestions": []})

    assert store.rows("task_cleanup_suggestions") == []
