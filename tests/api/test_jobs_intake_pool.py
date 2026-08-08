"""잡 접수 한 건이 원장 연결을 한 번에 하나만 빌리는지 동시 대여 수를 세는 대역으로 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest

from tests.support.chat_surface import SingleSql
from tests.support.fakes import FakeScanAnchors
from tests.support.services import fake_services
from tracer_agent.api import app as app_module
from tracer_agent.api.credentials import SettingModelCredentials
from tracer_agent.shared.agents.envelope.models import API_KEY_SETTING
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.settings.secret import SettingCipher
from tracer_agent.shared.config import MonitorProfile
from tracer_agent.shared.workflows.jobs_intake import JOBS_PATH

BASE_URL = "http://intake"
# 빌릴 자리가 없으면 대역은 무한히 기다리므로 이 여유를 넘긴 대여와 응답을 실패로 본다.
BORROW_TIMEOUT_S = 0.5
RESPONSE_TIMEOUT_S = 5.0
SEEDED_AT = "2026-07-30T00:00:00.000000"


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    ledger.seed(
        "app_settings",
        [{"scope": "local", "key": API_KEY_SETTING, "value": "sk-test", "updated_at": SEEDED_AT}],
    )
    yield ledger
    ledger.close()


def _client(store: SqliteLedgerSql, max_size: int) -> httpx.AsyncClient:
    source = SingleSql(store, max_size=max_size)
    cipher = SettingCipher(None, MonitorProfile.LOCAL)
    application = app_module.create_app()
    application.state.services = fake_services(
        execution_sql=source,
        setting_cipher=cipher,
        model_credentials=SettingModelCredentials(source, cipher),
        scan_anchors=FakeScanAnchors(),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=BASE_URL)


async def _borrow(source: SingleSql) -> None:
    async with source.connect():
        return


async def test_원장_대역은_정한_수를_넘겨_빌려_주지_않는다(store: SqliteLedgerSql) -> None:
    source = SingleSql(store, max_size=1)

    async with source.connect():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_borrow(source), BORROW_TIMEOUT_S)

    await asyncio.wait_for(_borrow(source), BORROW_TIMEOUT_S)


async def test_접수_한_건은_원장_연결을_동시에_둘_쥐지_않는다(store: SqliteLedgerSql) -> None:
    async with _client(store, max_size=1) as client:
        posted = client.post(JOBS_PATH, json={"kind": "recipe.scan", "input": {"taskId": "task-1"}})
        res = await asyncio.wait_for(posted, RESPONSE_TIMEOUT_S)

    assert res.status_code == 202
