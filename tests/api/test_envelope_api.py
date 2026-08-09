"""실행 봉투 창구가 계약이 정한 경로와 칸으로 카탈로그 값과 자격을 내는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.support.chat_surface import SingleSql
from tests.support.contract import conformance_case
from tests.support.services import fake_services
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.envelope.router import CHAT_KEY_MISSING, JOB_KEY_MISSING
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql

CHAT_PATH = "/internal/chat/executions/{execution_id}/envelope"
JOB_PATH = "/internal/jobs/{kind}/envelope"
REJECTION = conformance_case("job.intake")["response"]["envelopeRejection"]
NOW = datetime(2026, 7, 30, tzinfo=UTC)
API_KEY = "sk-ant-test"


class FakeCredentials:
    """설정 원장에 붙지 않고 미리 정한 자격과 모델만 내주는 창구 대역이다."""

    def __init__(self) -> None:
        self.stored: str | None = API_KEY
        self.model: str | None = None
        self.asked: list[str] = []

    async def api_key(self, user_id: str) -> str | None:
        self.asked.append(user_id)
        return self.stored

    async def chosen_model(self, _user_id: str) -> str | None:
        return self.model


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


@pytest.fixture
def credentials() -> FakeCredentials:
    return FakeCredentials()


@pytest.fixture
def client(store: SqliteLedgerSql, credentials: FakeCredentials) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.services = fake_services(
            execution_sql=SingleSql(store),
            model_credentials=credentials,
            read_api_base_url="http://tracer-api:3902",
            agent_api_base_url="http://agent-api:8800",
        )
        yield test_client


def seed_execution(store: SqliteLedgerSql, model: str | None = None) -> None:
    """봉투를 물을 대화 실행 행 하나를 기록한다."""
    store.seed(
        "chat_executions",
        [
            {
                "id": "e1",
                "user_id": "u1",
                "thread_id": "t1",
                "replay_anchor_message_id": "m1",
                "client_request_id": "c1",
                "input_hash": "h1",
                "status": "running",
                "model": model,
                "language": "ko",
                "created_at": NOW,
                "updated_at": NOW,
            }
        ],
    )


def test_대화_봉투는_계약이_정한_칸을_모두_싣는다(
    client: TestClient, store: SqliteLedgerSql, credentials: FakeCredentials
) -> None:
    seed_execution(store)

    res = client.post(CHAT_PATH.format(execution_id="e1"))

    assert res.status_code == 200
    data = res.json()["data"]
    assert list(data) == [
        "model",
        "apiKey",
        "modelRates",
        "limits",
        "deadlineMs",
        "readApiBaseUrl",
        "scopeToken",
        "toolDescriptions",
        "draft",
    ]
    assert data["model"] == "claude-sonnet-4-6"
    assert data["apiKey"] == API_KEY
    assert data["limits"] == {"budgetUsd": 1.2, "maxTurns": 14, "maxOutputTokens": 4000}
    assert data["deadlineMs"] == 600_000
    assert data["readApiBaseUrl"] == "http://tracer-api:3902"
    assert data["toolDescriptions"]["get_task"]
    assert credentials.asked == ["u1"]


def test_대화_봉투의_모델은_원장이_고른_값을_우선한다(client: TestClient, store: SqliteLedgerSql) -> None:
    seed_execution(store, model="claude-opus-5")

    assert client.post(CHAT_PATH.format(execution_id="e1")).json()["data"]["model"] == "claude-opus-5"


def test_대화_봉투는_시도마다_다른_draft_자격을_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    seed_execution(store)

    first = client.post(CHAT_PATH.format(execution_id="e1")).json()["data"]["draft"]
    second = client.post(CHAT_PATH.format(execution_id="e1")).json()["data"]["draft"]

    assert first["url"] == "http://agent-api:8800/api/agent/chat/executions/e1/drafts"
    assert first["token"] != second["token"]
    assert first["tokenHash"] != first["token"]


def test_없는_대화_실행은_404를_낸다(client: TestClient) -> None:
    res = client.post(CHAT_PATH.format(execution_id="no-such"))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_자격이_없으면_대화_봉투를_내지_않는다(
    client: TestClient, store: SqliteLedgerSql, credentials: FakeCredentials
) -> None:
    seed_execution(store)
    credentials.stored = None

    res = client.post(CHAT_PATH.format(execution_id="e1"))

    assert res.status_code == REJECTION["status"]
    assert res.json()["error"]["code"] == REJECTION["chat"]["code"]


def test_자격이_없으면_잡_봉투를_내지_않는다(client: TestClient, credentials: FakeCredentials) -> None:
    credentials.stored = None

    res = client.post(JOB_PATH.format(kind="recipe.scan"), json={"userId": "u1"})

    assert res.status_code == REJECTION["status"]
    assert res.json()["error"]["code"] == REJECTION["jobs"]["code"]


def test_자격_거절_코드가_창구마다_갈린다() -> None:
    assert (CHAT_KEY_MISSING[0], CHAT_KEY_MISSING[1]) == (REJECTION["status"], REJECTION["chat"]["code"])
    assert (JOB_KEY_MISSING[0], JOB_KEY_MISSING[1]) == (REJECTION["status"], REJECTION["jobs"]["code"])


def test_잡_봉투는_계약이_정한_칸을_모두_싣는다(client: TestClient) -> None:
    res = client.post(JOB_PATH.format(kind="recipe.scan"), json={"userId": "u1"})

    assert res.status_code == 200
    data = res.json()["data"]
    assert list(data) == ["model", "fallbackModel", "apiKey", "modelRates", "limits", "deadlineMs"]
    assert data["model"] == "claude-sonnet-4-6"
    assert data["fallbackModel"] == "claude-haiku-4-5"
    assert data["limits"] == {"budgetUsd": 2.0, "maxTurns": 15, "maxOutputTokens": 16000}
    assert data["deadlineMs"] == 720_000
    assert data["modelRates"]["claude-haiku-4-5"] == {
        "input": 1.0,
        "output": 5.0,
        "cacheWrite": 2.0,
        "cacheRead": 0.1,
    }


def test_잡_봉투는_종류마다_다른_기본_모델과_한도를_낸다(client: TestClient) -> None:
    data = client.post(JOB_PATH.format(kind="title.suggestion"), json={"userId": "u1"}).json()["data"]

    assert data["model"] == "claude-haiku-4-5"
    assert data["limits"]["budgetUsd"] == 0.2


def test_모르는_잡_종류는_400을_낸다(client: TestClient) -> None:
    res = client.post(JOB_PATH.format(kind="rule.generation"), json={"userId": "u1"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_사용자가_없는_본문은_400을_낸다(client: TestClient) -> None:
    res = client.post(JOB_PATH.format(kind="recipe.scan"), json={})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_잡_봉투는_설정이_고른_모델로_기본_모델을_덮는다(
    client: TestClient, credentials: FakeCredentials
) -> None:
    credentials.model = "claude-sonnet-5"

    data = client.post(JOB_PATH.format(kind="title.suggestion"), json={"userId": "u1"}).json()["data"]

    assert data["model"] == "claude-sonnet-5"


def test_설정이_모델을_덮어도_한도와_대체_모델은_종류가_갖는다(
    client: TestClient, credentials: FakeCredentials
) -> None:
    credentials.model = "claude-sonnet-5"

    data = client.post(JOB_PATH.format(kind="title.suggestion"), json={"userId": "u1"}).json()["data"]

    assert data["fallbackModel"] == "claude-haiku-4-5"
    assert data["limits"]["budgetUsd"] == 0.2
    assert data["deadlineMs"] == 300_000


def test_카탈로그가_모르는_모델_설정은_종류의_기본_모델로_본다(
    client: TestClient, credentials: FakeCredentials
) -> None:
    credentials.model = "gpt-9"

    data = client.post(JOB_PATH.format(kind="title.suggestion"), json={"userId": "u1"}).json()["data"]

    assert data["model"] == "claude-haiku-4-5"


def test_그_종류가_허용하지_않은_모델_설정은_기본_모델로_본다(
    client: TestClient, credentials: FakeCredentials
) -> None:
    credentials.model = "claude-opus-5"

    data = client.post(JOB_PATH.format(kind="title.suggestion"), json={"userId": "u1"}).json()["data"]

    assert data["model"] == "claude-haiku-4-5"


def test_턴_상한은_폭주만_끊도록_넉넉하고_비용은_달러가_조인다(client: TestClient) -> None:
    data = client.post(JOB_PATH.format(kind="title.suggestion"), json={"userId": "u1"}).json()["data"]

    assert data["limits"]["maxTurns"] >= 10
    assert data["limits"]["budgetUsd"] == 0.2
