"""브라우저가 직접 부르는 잡 접수 창구가 tracer-api와 같은 경로와 봉투를 내는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.support.contract import conformance_case
from tests.support.fakes import FakeScanAnchors
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND, declared_axes
from tracer_agent.shared.workflows.jobs_anchor import RuleAnchor, ScanAnchor

PATH = "/api/agent/jobs"
_INTAKE = conformance_case("job.intake")
JOB_FIELDS = _INTAKE["response"]["jobFields"]
CREDENTIAL_REJECTION = next(
    rejection
    for rejection in _INTAKE["rejections"]
    if rejection["code"] == _INTAKE["credentialCheck"]["rejection"]
)
LEASE_OWNER = _INTAKE["leaseOwner"]
LEASE_REJECTION = next(
    rejection for rejection in _INTAKE["rejections"] if rejection["code"] == LEASE_OWNER["rejection"]
)


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
    """워커에 실제로 붙지 않고 기동·취소 요청을 그대로 보관해 두는 잡 디스패치 대역이다."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str, dict[str, Any]]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.cancel_result = True

    async def start(self, kind: str, key: str, payload: dict[str, Any]) -> None:
        self.started.append((kind, key, payload))

    async def cancel(self, kind: str, key: str) -> bool:
        self.cancelled.append((kind, key))
        return self.cancel_result


class FakeCredentials:
    """설정 원장에 닿지 않고 미리 정한 모델 자격만 내주는 창구 대역이다."""

    def __init__(self) -> None:
        self.asked_for: list[str] = []
        self.key: str | None = "sk-test"

    async def api_key(self, user_id: str) -> str | None:
        self.asked_for.append(user_id)
        return self.key


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
def credentials() -> FakeCredentials:
    return FakeCredentials()


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


@pytest.fixture
def scan_anchors() -> FakeScanAnchors:
    return FakeScanAnchors()


@pytest.fixture
def client(
    dispatch: FakeJobDispatch,
    credentials: FakeCredentials,
    anchors: FakeRuleAnchors,
    scan_anchors: FakeScanAnchors,
    store: SqliteLedgerSql,
) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.job_dispatch = dispatch
        test_client.app.state.model_credentials = credentials
        test_client.app.state.rule_anchors = anchors
        test_client.app.state.scan_anchors = scan_anchors
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
    assert row["backend"] == AGENT_BACKEND
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
    # 사용자 전체를 조회하는 잡이라 태스크에 매이지 않는다.
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


def test_모델_자격이_없으면_접수를_거절한다(client: TestClient, credentials: FakeCredentials) -> None:
    credentials.key = None

    res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    assert res.status_code == CREDENTIAL_REJECTION["status"]
    assert res.json()["error"]["code"] == CREDENTIAL_REJECTION["code"]


def test_자격을_보지_않은_거절은_원장에_행을_남기지_않는다(
    client: TestClient, credentials: FakeCredentials, store: SqliteLedgerSql
) -> None:
    credentials.key = None

    client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    assert store.rows("ai_jobs") == []


_RULE_INPUT = {"taskId": "task-1", "anchorEventId": "ev-1", "intent": "테스트를 먼저 쓴다"}


def test_rule_generation_접수는_202와_로컬_실행기의_원장_행을_낸다(
    client: TestClient, dispatch: FakeJobDispatch, credentials: FakeCredentials, store: SqliteLedgerSql
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
    # 워크플로가 아니라 로컬 실행기가 실행해도 접수구가 정한 축은 같다.
    assert row["backend"] == AGENT_BACKEND
    assert row["task_id"] == "task-1"
    # 로컬 실행기가 가져가는 잡이라 워크플로도 실행 봉투도 접수가 부르지 않는다.
    assert dispatch.started == []
    assert credentials.asked_for == []


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


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        (ScanAnchor(id="task-1", origin="server-sdk", root=True, status="completed"), 400),
        (ScanAnchor(id="task-1", origin="user", root=False, status="completed"), 400),
        (ScanAnchor(id="task-1", origin="user", root=True, status="running"), 400),
        (ScanAnchor(id="task-1", origin="user", root=True, status="completed"), 202),
        (None, 400),
    ],
)
def test_스캔은_계약이_정한_앵커_자격만_접수한다(
    dispatch: FakeJobDispatch,
    credentials: FakeCredentials,
    anchors: FakeRuleAnchors,
    store: SqliteLedgerSql,
    anchor: ScanAnchor | None,
    expected: int,
) -> None:
    with TestClient(app_module.create_app()) as client:
        client.app.state.job_dispatch = dispatch
        client.app.state.model_credentials = credentials
        client.app.state.rule_anchors = anchors
        client.app.state.scan_anchors = FakeScanAnchors(anchor)
        client.app.state.execution_sql = SingleSql(store)

        res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    assert res.status_code == expected


def test_자격이_없는_앵커의_거절은_계약이_정한_코드로_나간다(
    dispatch: FakeJobDispatch,
    credentials: FakeCredentials,
    anchors: FakeRuleAnchors,
    store: SqliteLedgerSql,
) -> None:
    with TestClient(app_module.create_app()) as client:
        client.app.state.job_dispatch = dispatch
        client.app.state.model_credentials = credentials
        client.app.state.rule_anchors = anchors
        client.app.state.scan_anchors = FakeScanAnchors(None)
        client.app.state.execution_sql = SingleSql(store)

        res = client.post(PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    assert res.json()["error"]["code"] == "job.invalid-scan-anchor"


def test_세션에서_부른_스캔은_아직_도는_태스크도_접수한다(
    dispatch: FakeJobDispatch,
    credentials: FakeCredentials,
    anchors: FakeRuleAnchors,
    store: SqliteLedgerSql,
) -> None:
    running = ScanAnchor(id="task-1", origin="user", root=True, status="running")
    with TestClient(app_module.create_app()) as client:
        client.app.state.job_dispatch = dispatch
        client.app.state.model_credentials = credentials
        client.app.state.rule_anchors = anchors
        client.app.state.scan_anchors = FakeScanAnchors(running)
        client.app.state.execution_sql = SingleSql(store)

        res = client.post(
            PATH,
            json={"kind": "recipe.scan", "input": {"taskId": "task-1", "trigger": "session"}},
        )

    assert res.status_code == 202


# 본문을 요구하는 창구는 리스 이름이 아니라 본문 때문에 거절당하지 않도록 성립하는 본문을 싣는다.
REPORTED_USAGE = {"model": "claude", "durationMs": 10, "costUsd": 0.1, "numTurns": 1}
LEASE_BODIES = {
    "results": {"rules": [], "usage": REPORTED_USAGE, "steps": []},
    "fail": {"message": "boom", "usage": REPORTED_USAGE, "steps": []},
}


@pytest.mark.parametrize("declared", LEASE_OWNER["paths"])
def test_리스를_요구하는_창구는_이름_없는_요청을_계약의_낱말로_거절한다(
    client: TestClient, declared: str
) -> None:
    path = declared.replace("{id}", "no-such-run")

    res = client.post(path, json=LEASE_BODIES.get(declared.rsplit("/", 1)[-1]))

    assert res.status_code == LEASE_REJECTION["status"]
    assert res.json()["error"] == {
        "code": LEASE_REJECTION["code"],
        "message": LEASE_REJECTION["message"],
    }


@pytest.mark.parametrize("declared", ["results", "fail"])
def test_본문을_요구하는_창구도_계약이_정한_400으로_거절한다(client: TestClient, declared: str) -> None:
    # FastAPI 의 자동 검증에 맡기면 계약에 없는 422 가 나가 실행기가 거절을 가리지 못한다.
    res = client.post(
        f"{PATH}/no-such-run/{declared}",
        json={"쓸모없는칸": 1, "usage": REPORTED_USAGE, "steps": []},
        headers={"x-monitor-lease-owner": "runner-1"},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


LOCAL_EXECUTOR = _INTAKE["localExecutor"]


@pytest.mark.parametrize("declared", ["results", "fail"])
def test_관측_없는_보고를_거절한다(client: TestClient, declared: str) -> None:
    # 관측을 받지 않으면 로컬 실행기가 쓴 비용이 원장에 닿을 길이 없다.
    body = {key: value for key, value in LEASE_BODIES[declared].items() if key != "usage"}

    res = client.post(f"{PATH}/no-such-run/{declared}", json=body, headers={"x-monitor-lease-owner": "r1"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_실행기의_관측과_궤적이_원장에_실린다(client: TestClient) -> None:
    accepted = client.post(PATH, json={"kind": "rule.generation", "input": _RULE_INPUT}).json()
    run_id = accepted["data"]["job"]["id"]
    lease = {"x-monitor-lease-owner": "runner-1"}
    client.post(f"{PATH}/{run_id}/start", headers=lease)
    usage = {**REPORTED_USAGE, "inputTokens": 5}
    step = {"seq": 0, "role": "assistant", "content": "규칙을 쓴다", "truncated": False, "toolCalls": []}

    settled = client.post(
        f"{PATH}/{run_id}/results", json={"rules": [], "usage": usage, "steps": [step]}, headers=lease
    )

    assert settled.status_code == 200
    assert settled.json()["data"]["job"]["usage"] == usage
    steps = client.get(f"{PATH}/{run_id}/steps").json()["data"]
    assert [(item["seq"], item["role"], item["attempt"]) for item in steps] == [(0, "assistant", 1)]
