"""잡의 종료 전이와 궤적과 관측이 한 트랜잭션으로 함께 남는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.models import AgentStepDTO
from tracer_agent.shared.workflows.jobs_ledger import JobLedger
from tracer_agent.worker.workflows.jobs_writer import JobExecutionWriter, JobOutcome

NOW = datetime(2026, 7, 28, 0, 5, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


def observation(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "executionId": "j1",
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


async def claim(store: SqliteLedgerSql, job_id: str = "j1") -> None:
    """종결을 받을 대기 행 하나를 세운다."""
    await JobLedger(store).claim(job_id, "u1", "title.suggestion", "temporal", None, None, {}, NOW)


def outcome(**overrides: Any) -> JobOutcome:
    defaults: dict[str, Any] = {
        "job_id": "j1",
        "user_id": "u1",
        "status": "completed",
        "attempt": 1,
        "result": {"suggestions": []},
        "usage": {"costUsd": 0.01},
        "observation": observation(),
    }
    return JobOutcome(**(defaults | overrides))


async def test_종료_전이와_궤적과_관측이_함께_남는다(store: SqliteLedgerSql) -> None:
    await claim(store)

    settled = await JobExecutionWriter(store).finalize(
        outcome(steps=[AgentStepDTO(seq=0, role="assistant", content="done")]), NOW
    )

    assert settled is True
    row = store.rows("ai_jobs")[0]
    assert row["status"] == "completed"
    assert row["result"] == {"suggestions": []}
    assert row["usage"] == {"costUsd": 0.01}
    assert store.rows("ai_job_steps")[0]["content"] == "done"
    assert store.rows("agent_run_observations")[0]["execution_id"] == "j1"


async def test_이미_끝난_잡이면_관측만_남고_전이는_거짓을_낸다(store: SqliteLedgerSql) -> None:
    await claim(store)
    await JobLedger(store).settle("j1", "completed", {}, {}, None, NOW)

    settled = await JobExecutionWriter(store).finalize(
        outcome(status="failed", result={}, error="boom", observation=observation(status="failed")), NOW
    )

    assert settled is False
    assert store.rows("ai_jobs")[0]["status"] == "completed"


async def test_실패는_사유를_남긴다(store: SqliteLedgerSql) -> None:
    await claim(store, "j2")

    settled = await JobExecutionWriter(store).finalize(
        outcome(
            job_id="j2",
            status="failed",
            result={},
            error="boom",
            observation=observation(executionId="j2", status="failed"),
        ),
        NOW,
    )

    assert settled is True
    row = store.rows("ai_jobs")[0]
    assert row["status"] == "failed"
    assert row["error"] == "boom"


async def test_관측이_없어도_전이와_궤적은_남는다(store: SqliteLedgerSql) -> None:
    await claim(store)

    settled = await JobExecutionWriter(store).finalize(
        outcome(observation=None, steps=[AgentStepDTO(seq=0, role="assistant", content="done")]), NOW
    )

    assert settled is True
    assert store.rows("agent_run_observations") == []
    assert len(store.rows("ai_job_steps")) == 1
