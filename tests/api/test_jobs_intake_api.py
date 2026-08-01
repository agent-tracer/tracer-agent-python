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
from tracer_agent.shared.agents.shared.axis import AGENT_AXIS, declared_axes
from tracer_agent.shared.workflows.jobs_anchor import RuleAnchor
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
            deadline_ms=720_000,
        )


class FakeRuleAnchors:
    """미리 심어 둔 근거만 그 사용자에게 내주는 근거 창구 대역이다."""

    def __init__(self) -> None:
        self.anchors: dict[tuple[str, str], RuleAnchor] = {}

    def add(self, user_id: str, anchor: RuleAnchor) -> None:
        self.anchors[(user_id, anchor.id)] = anchor

    async def find(self, user_id: str, event_id: str) -> RuleAnchor | None:
        return self.anchors.get((user_id, event_id))


@pytest.fixture
def anchors() -> FakeRuleAnchors:
    source = FakeRuleAnchors()
    source.add("local", RuleAnchor(id="ev-1", task_id="task-1", user_message=True))
    return source


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
    dispatch: FakeJobDispatch,
    envelopes: FakeEnvelopes,
    anchors: FakeRuleAnchors,
    store: SqliteLedgerSql,
) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.job_dispatch = dispatch
        test_client.app.state.job_envelopes = envelopes
        test_client.app.state.rule_anchors = anchors
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
    # 데드라인은 실행 봉투가 소유하므로 접수가 값을 싣지 않는다.
    assert "deadlineMs" not in payload
    row = store.rows("ai_jobs")[0]
    assert row["status"] == "pending"
    assert row["kind"] == "recipe.scan"
    assert row["executor"] == "temporal"
    assert row["input"] == {"taskId": "task-1"}
    assert row["task_id"] == "task-1"


def test_대기_중인_잡도_접수구의_축을_원장에_갖는다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    job = res.json()["data"]["job"]
    row = store.rows("ai_jobs")[0]
    assert row["status"] == "pending"
    assert row["backend"] == AGENT_AXIS
    assert job["backend"] in declared_axes()


def test_접수_본문은_축을_싣지_못한다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}, "backend": "ts"})

    assert res.status_code == 400
    assert store.rows("ai_jobs") == []


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


def test_다듬기만_다른_입력은_같은_잡으로_본다(client: TestClient, store: SqliteLedgerSql) -> None:
    first = client.post(
        PATH,
        json={"kind": "recipe.scan", "input": {"taskId": "task-1"}, "idempotencyKey": "idem-1"},
    )
    second = client.post(
        PATH,
        json={"kind": "recipe.scan", "input": {"taskId": " task-1 "}, "idempotencyKey": "idem-1"},
    )

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


_RULE_INPUT = {"taskId": "task-1", "anchorEventId": "ev-1", "intent": "테스트를 먼저 쓴다"}


def test_rule_generation_접수는_202와_로컬_실행기의_원장_행을_낸다(
    client: TestClient, dispatch: FakeJobDispatch, envelopes: FakeEnvelopes, store: SqliteLedgerSql
) -> None:
    res = client.post(PATH, json={"kind": "rule.generation", "input": _RULE_INPUT})

    assert res.status_code == 202
    job = res.json()["data"]["job"]
    assert list(job) == JOB_FIELDS
    assert job["kind"] == "rule.generation"
    assert job["executor"] == "local"
    assert job["status"] == "pending"
    row = store.rows("ai_jobs")[0]
    assert row["executor"] == "local"
    # 워크플로가 아니라 로컬 실행기가 태워도 접수구가 정한 축은 같다.
    assert row["backend"] == AGENT_AXIS
    assert row["task_id"] == "task-1"
    # 로컬 실행기가 가져가는 잡이라 워크플로도 실행 봉투도 접수가 부르지 않는다.
    assert dispatch.started == []
    assert envelopes.issued_for == []


def test_rule_generation_잡은_대기_목록으로_실행기에_보인다(client: TestClient) -> None:
    client.post(PATH, json={"kind": "rule.generation", "input": _RULE_INPUT})

    res = client.get(PATH, params={"kind": "rule.generation", "status": "pending"})

    assert res.status_code == 200
    assert [item["kind"] for item in res.json()["data"]["items"]] == ["rule.generation"]


def test_없는_근거로_온_rule_generation은_400이다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.post(
        PATH,
        json={"kind": "rule.generation", "input": {**_RULE_INPUT, "anchorEventId": "ev-9"}},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "job.invalid-rule-anchor"
    assert store.rows("ai_jobs") == []


def test_다른_태스크의_근거는_거절한다(client: TestClient, anchors: FakeRuleAnchors) -> None:
    anchors.add("local", RuleAnchor(id="ev-2", task_id="task-2", user_message=True))

    res = client.post(
        PATH,
        json={"kind": "rule.generation", "input": {**_RULE_INPUT, "anchorEventId": "ev-2"}},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "job.invalid-rule-anchor"


def test_사용자_발화가_아닌_근거는_거절한다(client: TestClient, anchors: FakeRuleAnchors) -> None:
    anchors.add("local", RuleAnchor(id="ev-3", task_id="task-1", user_message=False))

    res = client.post(
        PATH,
        json={"kind": "rule.generation", "input": {**_RULE_INPUT, "anchorEventId": "ev-3"}},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "job.invalid-rule-anchor"


def test_남의_근거는_없는_것으로_본다(client: TestClient) -> None:
    res = client.post(
        PATH, json={"kind": "rule.generation", "input": _RULE_INPUT}, headers={"x-monitor-user": "u2"}
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "job.invalid-rule-anchor"


def test_근거를_싣지_않은_rule_generation은_스키마에서_막힌다(client: TestClient) -> None:
    res = client.post(PATH, json={"kind": "rule.generation", "input": {"taskId": "task-1"}})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_로컬_잡의_취소는_워크플로_취소를_부르지_않는다(
    client: TestClient, dispatch: FakeJobDispatch
) -> None:
    accepted = client.post(PATH, json={"kind": "rule.generation", "input": _RULE_INPUT}).json()

    res = client.post(f"{PATH}/{accepted['data']['job']['id']}/cancel")

    assert res.status_code == 200
    assert res.json()["data"]["job"]["status"] == "canceled"
    assert dispatch.cancelled == []
