"""잡의 종료 전이와 관측이 한 트랜잭션으로 함께 남거나 함께 사라지는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.workflows.jobs_ledger import GraphJobLedger
from tracer_agent.worker.workflows.jobs_writer import GraphJobExecutionWriter

NOW = datetime(2026, 7, 28, 0, 5, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


def observation(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "executionId": "e1",
        "attemptId": "1",
        "jobId": None,
        "agentName": "title-suggestion",
        "backend": "python",
        "modelRequested": "model",
        "modelActual": "model",
        "promptVersion": "1.0.0",
        "promptContentHash": "sha256:test",
        "toolContractVersion": "1.0.0",
        "status": "succeeded",
        "durationMs": 1,
        "usage": {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0},
        "costUsd": 0.01,
        "landed": True,
        "repairAttempted": False,
        "validation": {"passed": True, "errorCodes": [], "citationPrecision": None, "citationRecall": None},
        "modelCalls": [],
        "toolCalls": [],
    }
    return defaults | overrides


async def test_종료_전이와_관측이_함께_남는다(store: SqliteLedgerSql) -> None:
    await GraphJobLedger(store).claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW)

    settled = await GraphJobExecutionWriter(store).finalize(
        "e1", "u1", "completed", 0.01, None, observation(), NOW
    )

    assert settled is True
    assert store.rows("graph_job_executions")[0]["status"] == "completed"
    assert store.rows("agent_run_observations")[0]["execution_id"] == "e1"


async def test_이미_끝난_행이면_관측만_남고_전이는_거짓을_낸다(store: SqliteLedgerSql) -> None:
    await GraphJobLedger(store).claim("e1", "u1", "title-suggestion", None, None, 2.0, NOW)
    await GraphJobLedger(store).settle("e1", "completed", 0.01, None, NOW)

    settled = await GraphJobExecutionWriter(store).finalize(
        "e1", "u1", "failed", None, "boom", observation(status="failed"), NOW
    )

    assert settled is False
    assert store.rows("graph_job_executions")[0]["status"] == "completed"


async def test_완료는_구조화_결과도_함께_남긴다(store: SqliteLedgerSql) -> None:
    await GraphJobLedger(store).claim("e3", "u1", "title-suggestion", None, "task-1", 2.0, NOW)

    settled = await GraphJobExecutionWriter(store).finalize(
        "e3", "u1", "completed", 0.01, None, observation(executionId="e3"), NOW, {"suggestions": []}
    )

    assert settled is True
    assert store.rows("graph_job_executions")[0]["result"] == {"suggestions": []}


async def test_실패는_사유를_남긴다(store: SqliteLedgerSql) -> None:
    await GraphJobLedger(store).claim("e2", "u1", "recipe-scan", None, None, 2.0, NOW)

    settled = await GraphJobExecutionWriter(store).finalize(
        "e2", "u1", "failed", None, "boom", observation(executionId="e2", status="failed"), NOW
    )

    assert settled is True
    row = store.rows("graph_job_executions")[0]
    assert row["status"] == "failed"
    assert row["error"] == "boom"
