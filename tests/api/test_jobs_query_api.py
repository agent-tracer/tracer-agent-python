"""잡 조회 창구가 원장 한 벌을 계약이 정한 칸과 순서로 내는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.support.contract import conformance_case
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.shared.models import AgentStepDTO, AgentStepToolCall
from tracer_agent.shared.workflows.jobs_ledger import JobLedger

PATH = "/api/v1/jobs"
_RESPONSE = conformance_case("job.intake")["response"]
JOB_FIELDS = _RESPONSE["jobFields"]
REQUIRED_STEP_FIELDS = _RESPONSE["steps"]["required"]
OPTIONAL_STEP_FIELDS = _RESPONSE["steps"]["optional"]
NOW = datetime(2026, 7, 30, tzinfo=UTC)


class SingleSql:
    """테스트 하나가 쓰는 메모리 원장을 조회 창구에 그대로 빌려 준다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """빌릴 때마다 같은 메모리 원장을 낸다."""
        return self._lend()

    @asynccontextmanager
    async def _lend(self) -> AsyncIterator[LedgerSql]:
        yield self._store


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


@pytest.fixture
def client(store: SqliteLedgerSql) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.execution_sql = SingleSql(store)
        yield test_client


async def seed_job(store: SqliteLedgerSql, user_id: str = "local") -> None:
    """조회가 읽을 잡 한 행과 그 궤적을 심는다."""
    ledger = JobLedger(store)
    await ledger.claim("j1", user_id, "recipe.scan", "temporal", "task-1", None, {"taskId": "task-1"}, NOW)
    await ledger.mark_running("j1", NOW)
    await ledger.record_steps(
        "j1",
        user_id,
        2,
        [AgentStepDTO(seq=0, role="graph", content="retried", nodeName="survey", eventKind="node.started")],
        NOW,
    )
    await ledger.record_steps(
        "j1",
        user_id,
        1,
        [
            AgentStepDTO(
                seq=0,
                role="assistant",
                content="calling",
                toolCalls=[AgentStepToolCall(id="c1", name="get_task", args={"taskId": "task-1"})],
                outputTokens=11,
                stopReason="tool_use",
            ),
            AgentStepDTO(seq=1, role="tool", content="done", toolName="get_task", toolCallId="c1"),
        ],
        NOW,
    )
    await ledger.settle("j1", "completed", {"recipes": []}, {"costUsd": 0.2}, None, NOW)


async def test_잡_조회는_원장_행을_계약이_정한_칸으로_낸다(
    client: TestClient, store: SqliteLedgerSql
) -> None:
    await seed_job(store)

    res = client.get(f"{PATH}/j1")

    assert res.status_code == 200
    job = res.json()["data"]["job"]
    assert list(job) == JOB_FIELDS
    assert job["status"] == "completed"
    assert job["attempts"] == 1
    assert job["taskId"] == "task-1"
    assert job["result"] == {"recipes": []}
    assert job["usage"] == {"costUsd": 0.2}
    assert job["error"] is None


async def test_없는_잡_조회는_404다(client: TestClient) -> None:
    res = client.get(f"{PATH}/no-such")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_남의_잡은_존재_여부도_드러내지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    await seed_job(store, user_id="u2")

    assert client.get(f"{PATH}/j1").status_code == 404
    assert client.get(f"{PATH}/j1/steps").status_code == 404
    assert client.post(f"{PATH}/j1/cancel").status_code == 404


async def test_궤적_조회는_시도와_순번의_오름차순으로_낸다(
    client: TestClient, store: SqliteLedgerSql
) -> None:
    await seed_job(store)

    res = client.get(f"{PATH}/j1/steps")

    assert res.status_code == 200
    steps = res.json()["data"]
    assert [(step["attempt"], step["seq"]) for step in steps] == [(1, 0), (1, 1), (2, 0)]


async def test_궤적_한_줄은_값이_있는_자리만_싣는다(client: TestClient, store: SqliteLedgerSql) -> None:
    await seed_job(store)

    steps = client.get(f"{PATH}/j1/steps").json()["data"]

    assert all(set(REQUIRED_STEP_FIELDS) <= set(step) for step in steps)
    assert all(set(step) <= set(REQUIRED_STEP_FIELDS) | set(OPTIONAL_STEP_FIELDS) for step in steps)
    assert steps[0]["toolCalls"] == [{"id": "c1", "name": "get_task", "args": {"taskId": "task-1"}}]
    assert steps[0]["outputTokens"] == 11
    assert steps[0]["stopReason"] == "tool_use"
    assert "toolName" not in steps[0]
    assert steps[1]["toolCalls"] == []
    assert steps[1]["toolName"] == "get_task"
    assert steps[2]["eventKind"] == "node.started"
    assert steps[2]["nodeName"] == "survey"


async def test_궤적이_없는_잡은_빈_목록을_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    await JobLedger(store).claim("j2", "local", "task.cleanup", "temporal", None, None, {}, NOW)

    res = client.get(f"{PATH}/j2/steps")

    assert res.status_code == 200
    assert res.json() == {"ok": True, "data": []}
