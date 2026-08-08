"""드라이버 오류가 원장 구현이 다룰 수 있는 결과로 바뀌는지 검증한다."""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from tracer_agent.shared.agents.runtime import ledger
from tracer_agent.shared.agents.runtime.ledger import (
    AsyncpgSql,
    LedgerPoolProvider,
    LedgerUnavailable,
    PooledSql,
    UniqueViolation,
    acquire_sql,
)


class StubConnection:
    """준비된 결과나 오류만 내는 asyncpg 연결 대역이다."""

    def __init__(self, rows: list[Any] | None = None, failure: Exception | None = None) -> None:
        self._rows = rows or []
        self._failure = failure

    async def fetch(self, _sql: str, *_args: Any) -> list[Any]:
        """준비된 행을 내거나 준비된 오류를 낸다."""
        if self._failure is not None:
            raise self._failure
        return self._rows


class StubPool:
    """빌려 달라고 받은 여유를 기억하고 준비된 연결이 없으면 드라이버처럼 대기 만료를 내는 풀 대역이다."""

    def __init__(self, connection: StubConnection | None = None) -> None:
        self._connection = connection
        self.timeouts: list[float | None] = []
        self.released: list[Any] = []

    async def acquire(self, timeout: float | None = None) -> StubConnection:
        """받은 여유를 적고 준비된 연결을 내거나 대기 만료를 낸다."""
        self.timeouts.append(timeout)
        if self._connection is None:
            raise TimeoutError
        return self._connection

    async def release(self, connection: Any) -> None:
        """반납된 연결을 적는다."""
        self.released.append(connection)


class StubProvider:
    """이미 열린 풀 하나를 그대로 내주는 풀 제공자 대역이다."""

    def __init__(self, pool: StubPool) -> None:
        self._pool = pool

    async def pool(self) -> StubPool:
        """세워 둔 풀을 낸다."""
        return self._pool


async def test_정해진_여유_안에_연결을_빌리지_못하면_영구_대기가_아니라_거절로_낸다() -> None:
    pool = StubPool()

    with pytest.raises(LedgerUnavailable):
        async with acquire_sql(pool, 0.25):  # type: ignore[arg-type]
            pass

    assert pool.timeouts == [0.25]


async def test_빌린_연결은_쓰임이_끝나면_배포가_정한_여유와_함께_반납된다() -> None:
    connection = StubConnection(rows=[{"id": "e1"}])
    pool = StubPool(connection)

    async with PooledSql(StubProvider(pool), 1.5).connect() as sql:  # type: ignore[arg-type]
        assert await sql.fetch("SELECT 1") == [{"id": "e1"}]

    assert pool.timeouts == [1.5]
    assert pool.released == [connection]


async def test_유일_제약_위반은_원장_오류로_바뀐다() -> None:
    sql = AsyncpgSql(StubConnection(failure=asyncpg.UniqueViolationError("duplicate key")))  # type: ignore[arg-type]

    with pytest.raises(UniqueViolation):
        await sql.fetch("UPDATE chat_executions SET status = 'running'")


async def test_돌아온_행은_사전으로_낸다() -> None:
    sql = AsyncpgSql(StubConnection(rows=[{"id": "e1"}]))  # type: ignore[arg-type]

    assert await sql.fetch("SELECT 1") == [{"id": "e1"}]


async def test_풀을_열_때_배포가_정한_크기를_그대로_드라이버에_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: dict[str, Any] = {}

    async def fake_create_pool(dsn: str, **kwargs: Any) -> object:
        opened.update({"dsn": dsn, **kwargs})
        return object()

    monkeypatch.setattr(ledger.asyncpg, "create_pool", fake_create_pool)

    await LedgerPoolProvider("postgresql://app@db:5432/agent", min_size=2, max_size=32).pool()

    assert opened["min_size"] == 2
    assert opened["max_size"] == 32


async def test_풀_크기를_스스로_갖지_않는다() -> None:
    # 상한을 모듈 상수로 두면 배포가 조정할 수도 검토할 수도 없는 값이 하나 남는다.
    with pytest.raises(TypeError):
        LedgerPoolProvider("postgresql://app@db:5432/agent")  # type: ignore[call-arg]
