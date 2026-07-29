"""부팅 검사가 코드 pin과 DB 계약 뷰의 production 채널을 대조만 하고 쓰지 않는지 검증한다."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest

from tracer_agent.worker.prompt_registry.check import (
    PromptRegistryOutOfSyncError,
    assert_prompt_registry_synced,
)
from tracer_agent.worker.prompt_registry.pin import resolve_pinned_prompt_registrations

_PINS = {pin.agent_name: pin for pin in resolve_pinned_prompt_registrations()}


class FakeLedgerSql:
    """계약 뷰의 행을 흉내 내며 실행된 문장을 그대로 기록한다."""

    def __init__(self, rows: dict[str, dict[str, str]]) -> None:
        self._rows = rows
        self.statements: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        self.statements.append(sql)
        row = self._rows.get(str(args[0]))
        return [] if row is None else [row]

    def transaction(self) -> AbstractAsyncContextManager[None]:
        raise NotImplementedError("검사는 문장 하나만 읽으며 트랜잭션을 열지 않는다")


class UnreachableLedgerSql:
    """연결이 끊긴 원장을 흉내 내 문장 실행이 그대로 예외로 터진다."""

    async def fetch(self, _sql: str, *_args: Any) -> list[dict[str, str]]:
        raise ConnectionError("boom")

    def transaction(self) -> AbstractAsyncContextManager[None]:
        raise NotImplementedError


def _synced_rows() -> dict[str, dict[str, str]]:
    return {
        agent_name: {"semantic_version": pin.semantic_version, "content_hash": pin.resolved_prompt_hash}
        for agent_name, pin in _PINS.items()
    }


async def test_코드_pin과_DB가_같으면_통과한다() -> None:
    await assert_prompt_registry_synced(FakeLedgerSql(_synced_rows()))


async def test_쓰기는_한_번도_보내지_않는다() -> None:
    sql = FakeLedgerSql(_synced_rows())

    await assert_prompt_registry_synced(sql)

    assert sql.statements and all(
        statement.strip().upper().startswith("SELECT") for statement in sql.statements
    )


async def test_production_채널이_없으면_기동을_거부한다() -> None:
    with pytest.raises(PromptRegistryOutOfSyncError):
        await assert_prompt_registry_synced(FakeLedgerSql({}))


async def test_해시가_다르면_기동을_거부한다() -> None:
    rows = _synced_rows()
    agent_name = next(iter(rows))
    rows[agent_name]["content_hash"] = "0" * 64

    with pytest.raises(PromptRegistryOutOfSyncError):
        await assert_prompt_registry_synced(FakeLedgerSql(rows))


async def test_원장에_닿지_못하면_기동을_거부한다() -> None:
    with pytest.raises(PromptRegistryOutOfSyncError):
        await assert_prompt_registry_synced(UnreachableLedgerSql())
