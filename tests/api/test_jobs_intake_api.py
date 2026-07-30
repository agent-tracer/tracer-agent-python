"""브라우저가 직접 치는 잡 접수 창구가 tracer-api와 같은 경로와 봉투를 내는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from temporalio.exceptions import ApplicationError

from tests.support.contract import conformance_case
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.workflows.jobs_envelope import JobExecutionEnvelope

PATH = "/api/agent/jobs"
JOB_FIELDS = conformance_case("job.intake")["response"]["jobFields"]


class SingleSql:
    """테스트 하나가 쓰는 메모리 원장을 접수 창구에 그대로 빌려 준다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        return self._lend()

    @asynccontextmanager
    async def _lend(self) -> AsyncIterator[LedgerSql]:
        yield self._store


class FakeJobDispatch:
    """워커에 실제로 붙지 않고 기동·취소 요청을 그대로 붙잡아 두는 잡 디스패치 대역이다."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str, dict[str, Any]]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.cancel_result = True

    async def start(self, kind: str, key: str, payload: dict[str, Any]) -> None:
        self.started.append((kind, key, payload))

    async def cancel(self, kind: str, key: str) -> bool:
        self.cancelled.append((kind, key))
        return self.cancel_result


class FakeEnvelopes:
    """카탈로그 값을 지어내지 않고 미리 정한 값만 내주는 봉투 창구 대역이다."""

    def __init__(self) -> None:
        self.issued_for: list[tuple[str, str]] = []
        self.unavailable = False

    async def issue(self, kind: str, user_id: str) -> JobExecutionEnvelope:
        if self.unavailable:
            raise ApplicationError("job envelope HTTP 404", type="job.envelope-unavailable")
        self.issued_for.append((kind, user_id))
        return JobExecutionEnvelope(
            model="claude-sonnet-4-6",
            fallback_model=None,
            api_key="sk-test",
            model_rates={},
            limits={"budgetUsd": 2.0, "maxTurns": 16, "maxOutputTokens": 16_000},
        )


@pytest.fixture
def dispatch() -> FakeJobDispatch:
    return FakeJobDispatch()


@pytest.fixture
def envelopes() -> FakeEnvelopes:
    return FakeEnvelopes()


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


@pytest.fixture
def client(
    dispatch: FakeJobDispatch, envelopes: FakeEnvelopes, store: SqliteLedgerSql
) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.job_dispatch = dispatch
        test_client.app.state.job_envelopes = envelopes
        test_client.app.state.execution_sql = SingleSql(store)
        yield test_client


def test_recipe_scan_접수는_202와_원장_행을_낸다(
    client: TestClient, dispatch: FakeJobDispatch, store: SqliteLedgerSql
) -> None:
    res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    assert res.status_code == 202
    body = res.json()
    assert body["ok"] is True
    job = body["data"]["job"]
    assert list(job) == JOB_FIELDS
    assert job["kind"] == "recipe.scan"
    assert job["executor"] == "temporal"
    assert job["status"] == "pending"
    assert job["attempts"] == 0
    assert job["input"] == {"taskId": "task-1"}
    assert job["result"] == {} and job["usage"] == {}
    assert job["startedAt"] is None and job["completedAt"] is None
    assert job["createdAt"].endswith("Z")
    kind, key, payload = dispatch.started[0]
    assert kind == "recipe-scan"
    assert key == job["id"]
    assert payload["taskId"] == "task-1"
    assert payload["userId"] == "local"
    assert "apiKey" not in payload
    row = store.rows("ai_jobs")[0]
    assert row["status"] == "pending"
    assert row["kind"] == "recipe.scan"
    assert row["executor"] == "temporal"
    assert row["input"] == {"taskId": "task-1"}
    assert row["task_id"] == "task-1"


def test_자기신고_헤더가_사용자를_정한다(client: TestClient, dispatch: FakeJobDispatch) -> None:
    client.post(
        PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}}, headers={"x-monitor-user": "u2"}
    )

    _kind, _key, payload = dispatch.started[0]
    assert payload["userId"] == "u2"


def test_접수가_잡_식별자를_만들고_멱등키를_그대로_쓰지_않는다(
    client: TestClient, dispatch: FakeJobDispatch, store: SqliteLedgerSql
) -> None:
    res = client.post(
        PATH,
        json={"kind": "recipe.scan", "input": {"taskId": "task-1"}, "idempotencyKey": "idem-1"},
    )

    job_id = res.json()["data"]["job"]["id"]
    assert job_id != "idem-1"
    _kind, key, _payload = dispatch.started[0]
    assert key == job_id
    row = store.rows("ai_jobs")[0]
    assert row["id"] == job_id
    assert row["idempotency_key"] == "idem-1"


def test_같은_멱등키에_같은_입력이면_먼저_만든_잡을_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    body = {"kind": "recipe.scan", "input": {"taskId": "task-1"}, "idempotencyKey": "idem-1"}

    first = client.post(PATH, json=body)
    second = client.post(PATH, json=body)

    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json()["data"]["job"]["id"] == second.json()["data"]["job"]["id"]
    assert len(store.rows("ai_jobs")) == 1


def test_같은_멱등키에_다른_입력이면_409를_낸다(
    client: TestClient, dispatch: FakeJobDispatch, store: SqliteLedgerSql
) -> None:
    client.post(
        PATH,
        json={"kind": "recipe.scan", "input": {"taskId": "task-1"}, "idempotencyKey": "idem-1"},
    )

    res = client.post(
        PATH,
        json={"kind": "recipe.scan", "input": {"taskId": "task-2"}, "idempotencyKey": "idem-1"},
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "job.idempotency-conflict"
    assert len(store.rows("ai_jobs")) == 1
    assert len(dispatch.started) == 1


def test_title_suggestion_접수는_202와_실행_식별자를_낸다(
    client: TestClient, dispatch: FakeJobDispatch
) -> None:
    res = client.post(PATH, json={"kind": "title.suggestion", "input": {"taskId": "task-1"}})

    assert res.status_code == 202
    assert res.json()["ok"] is True
    kind, _key, payload = dispatch.started[0]
    assert kind == "title-suggestion"
    assert payload["taskId"] == "task-1"
    assert "context" not in payload
    assert "apiKey" not in payload


def test_task_cleanup_접수는_202와_실행_식별자를_낸다(
    client: TestClient, dispatch: FakeJobDispatch, store: SqliteLedgerSql
) -> None:
    res = client.post(PATH, json={"kind": "task.cleanup", "input": {}})

    assert res.status_code == 202
    assert res.json()["ok"] is True
    kind, _key, payload = dispatch.started[0]
    assert kind == "task-cleanup"
    assert payload["maxSuggestions"] == 20
    assert "batch" not in payload
    # 사용자 전체를 훑는 잡이라 태스크에 매이지 않는다.
    assert store.rows("ai_jobs")[0]["task_id"] is None


def test_task_cleanup_접수는_maxSuggestions를_받는다(client: TestClient, dispatch: FakeJobDispatch) -> None:
    client.post(PATH, json={"kind": "task.cleanup", "input": {"filters": {"maxSuggestions": 5}}})

    _kind, _key, payload = dispatch.started[0]
    assert payload["maxSuggestions"] == 5


def test_본문이_스키마를_어기면_400_오류_봉투를_낸다(client: TestClient) -> None:
    res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": ""}})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_본문이_JSON_객체가_아니면_400_오류_봉투를_낸다(client: TestClient) -> None:
    res = client.post(PATH, content=b"[]", headers={"content-type": "application/json"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_취소는_원장의_잡_종류로_워크플로_취소를_요청한다(
    client: TestClient, dispatch: FakeJobDispatch
) -> None:
    accepted = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}}).json()
    run_id = accepted["data"]["job"]["id"]

    res = client.post(f"{PATH}/{run_id}/cancel")

    assert res.status_code == 200
    job = res.json()["data"]["job"]
    assert list(job) == JOB_FIELDS
    assert job["status"] == "canceled"
    assert job["completedAt"] is not None
    assert dispatch.cancelled == [("recipe-scan", run_id)]


def test_없는_실행의_취소는_404다(client: TestClient) -> None:
    res = client.post(f"{PATH}/no-such-run/cancel")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_봉투를_못_받으면_502_오류_봉투를_낸다(client: TestClient, envelopes: FakeEnvelopes) -> None:
    envelopes.unavailable = True

    res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    assert res.status_code == 502
    assert res.json()["error"]["code"] == "job.envelope-unavailable"
