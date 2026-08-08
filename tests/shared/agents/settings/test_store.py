"""설정 원장의 두 리더가 자격 키를 서로 다르게 다루는지 검증한다."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.settings.secret import SettingCipher
from tracer_agent.shared.agents.settings.store import AppSettingStore, PlainSettingReader

API_KEY = "anthropic.api_key"
MODEL = "anthropic.model"
SCOPE = "u1"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def store() -> SqliteLedgerSql:
    return SqliteLedgerSql()


def _cipher() -> SettingCipher:
    return SettingCipher(None, "local")


async def test_평문_리더는_자격_키를_읽지_않고_거절한다(store: SqliteLedgerSql) -> None:
    await AppSettingStore(store, _cipher()).save(SCOPE, API_KEY, "sk-ant-secret-9876", NOW)

    with pytest.raises(ValueError, match=API_KEY):
        await PlainSettingReader(store).read(SCOPE, API_KEY)


async def test_평문_리더는_자격이_아닌_설정을_그대로_읽는다(store: SqliteLedgerSql) -> None:
    await AppSettingStore(store, _cipher()).save(SCOPE, MODEL, "claude-sonnet-4-6", NOW)

    assert await PlainSettingReader(store).read(SCOPE, MODEL) == "claude-sonnet-4-6"


async def test_저장한_적_없는_설정은_비운다(store: SqliteLedgerSql) -> None:
    assert await PlainSettingReader(store).read(SCOPE, MODEL) is None
