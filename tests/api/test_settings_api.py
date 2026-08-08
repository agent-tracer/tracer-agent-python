"""설정 창구가 계약이 정한 경로와 상태와 봉투를 내는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.support.services import fake_services
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.settings.secret import SettingCipher, is_encrypted_secret

SETTINGS_PATH = "/api/agent/settings"
MODELS_PATH = "/api/agent/settings/models"
API_KEY = "sk-ant-abcdefgh1234"
SEEDED_AT = datetime(2026, 7, 30, tzinfo=UTC)


class SingleSql:
    """테스트 하나가 쓰는 메모리 원장을 설정 창구에 그대로 빌려 준다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """빌릴 때마다 같은 메모리 원장을 낸다."""
        return self._lend()

    @asynccontextmanager
    async def _lend(self) -> AsyncIterator[LedgerSql]:
        yield self._store


@pytest.fixture
def cipher() -> SettingCipher:
    return SettingCipher("test-key", "prd")


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


@pytest.fixture
def client(store: SqliteLedgerSql, cipher: SettingCipher) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.services = fake_services(execution_sql=SingleSql(store), setting_cipher=cipher)
        yield test_client


def seed(store: SqliteLedgerSql, key: str, value: str) -> None:
    """이미 저장되어 있던 설정 하나를 기록한다."""
    store.seed("app_settings", [{"scope": "local", "key": key, "value": value, "updated_at": SEEDED_AT}])


def test_저장된_설정을_키마다_하나씩_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    seed(store, "anthropic.model", "claude-sonnet-5")

    res = client.get(SETTINGS_PATH)

    assert res.status_code == 200
    assert res.json() == {
        "ok": True,
        "data": {
            "items": [
                {
                    "key": "anthropic.model",
                    "maskedValue": "claude-sonnet-5",
                    "hasValue": True,
                    "updatedAt": "2026-07-30T00:00:00.000Z",
                }
            ]
        },
    }


def test_감춰_저장된_자격을_가린_값으로_낸다(
    client: TestClient, store: SqliteLedgerSql, cipher: SettingCipher
) -> None:
    seed(store, "anthropic.api_key", cipher.encrypt(API_KEY))

    res = client.get(SETTINGS_PATH)

    assert res.json()["data"]["items"][0]["maskedValue"] == "••••••••1234"


def test_카탈로그_밖의_키가_원장에_남아_있어도_싣지_않는다(
    client: TestClient, store: SqliteLedgerSql
) -> None:
    seed(store, "anthropic.apiKey", "stale")

    assert res_items(client) == []


def test_다른_사용자의_설정은_보이지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    seed(store, "anthropic.model", "claude-sonnet-5")

    res = client.get(SETTINGS_PATH, headers={"x-monitor-user": "other"})

    assert res.json()["data"]["items"] == []


def test_고를_수_있는_모델을_이름_오름차순으로_낸다(client: TestClient) -> None:
    res = client.get(MODELS_PATH)

    assert res.status_code == 200
    items = res.json()["data"]["items"]
    assert [item["id"] for item in items] == sorted(item["id"] for item in items)
    assert {"id", "label"} == set(items[0])


def test_쓴_설정을_가린_값으로_돌려준다(client: TestClient) -> None:
    res = client.put(f"{SETTINGS_PATH}/anthropic.api_key", json={"value": API_KEY})

    assert res.status_code == 200
    written = res.json()["data"]
    assert written["key"] == "anthropic.api_key"
    assert written["maskedValue"] == "••••••••1234"
    assert written["hasValue"] is True


def test_자격은_감춘_형식으로만_원장에_남는다(client: TestClient, store: SqliteLedgerSql) -> None:
    client.put(f"{SETTINGS_PATH}/anthropic.api_key", json={"value": API_KEY})

    stored = str(store.rows("app_settings")[0]["value"])
    assert is_encrypted_secret(stored)
    assert API_KEY not in stored


def test_자격이_아닌_값은_평문으로_남는다(client: TestClient, store: SqliteLedgerSql) -> None:
    client.put(f"{SETTINGS_PATH}/claude.outputLanguage", json={"value": "ko"})

    assert store.rows("app_settings")[0]["value"] == "ko"


def test_같은_키를_다시_쓰면_값을_갈아_끼운다(client: TestClient, store: SqliteLedgerSql) -> None:
    client.put(f"{SETTINGS_PATH}/claude.outputLanguage", json={"value": "ko"})
    client.put(f"{SETTINGS_PATH}/claude.outputLanguage", json={"value": "en"})

    rows = store.rows("app_settings")
    assert len(rows) == 1
    assert rows[0]["value"] == "en"


def test_단가를_아는_모델은_저장한다(client: TestClient) -> None:
    res = client.put(f"{SETTINGS_PATH}/anthropic.model", json={"value": "claude-sonnet-5"})

    assert res.status_code == 200
    assert res.json()["data"]["maskedValue"] == "claude-sonnet-5"


def test_단가를_모르는_모델은_거절하고_남기지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.put(f"{SETTINGS_PATH}/anthropic.model", json={"value": "claude-unknown"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert res.json()["error"]["details"][0]["type"] == "unpriced_model"
    assert store.rows("app_settings") == []


def test_카탈로그_밖의_키는_쓰지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.put(f"{SETTINGS_PATH}/anthropic.apiKey", json={"value": "x"})

    assert res.status_code == 400
    assert store.rows("app_settings") == []


def test_빈_값은_쓰지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.put(f"{SETTINGS_PATH}/claude.outputLanguage", json={"value": ""})

    assert res.status_code == 400
    assert store.rows("app_settings") == []


def test_저장된_설정을_지우고_지웠다고_알린다(client: TestClient, store: SqliteLedgerSql) -> None:
    seed(store, "anthropic.model", "claude-sonnet-5")

    res = client.delete(f"{SETTINGS_PATH}/anthropic.model")

    assert res.status_code == 200
    assert res.json()["data"] == {"key": "anthropic.model", "deleted": True}
    assert store.rows("app_settings") == []


def test_저장된_것이_없으면_지우지_않았다고_알린다(client: TestClient) -> None:
    res = client.delete(f"{SETTINGS_PATH}/anthropic.model")

    assert res.json()["data"] == {"key": "anthropic.model", "deleted": False}


def test_카탈로그_밖의_키는_지우지_않는다(client: TestClient) -> None:
    assert client.delete(f"{SETTINGS_PATH}/anthropic.apiKey").status_code == 400


def res_items(client: TestClient) -> list[dict[str, object]]:
    """설정 목록 응답에서 항목만 꺼낸다."""
    items = client.get(SETTINGS_PATH).json()["data"]["items"]
    return list(items)
