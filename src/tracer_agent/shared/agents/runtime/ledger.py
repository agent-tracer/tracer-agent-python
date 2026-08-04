"""원장에 연결되는 연결 풀의 수명과 드라이버를 가린 문장 실행 표면을 소유한다."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

import asyncpg
from asyncpg.pool import PoolConnectionProxy

# 런타임의 Pool은 첨자를 받지 않으므로 평가가 지연되는 별칭으로만 제네릭 형태를 쓴다.
type LedgerPool = asyncpg.Pool[asyncpg.Record]

# 풀에서 받은 연결은 대리자로 오므로 두 형태를 모두 받는다.
type LedgerConnection = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

type SqlRow = dict[str, Any]

MIN_POOL_SIZE = 1
MAX_POOL_SIZE = 8


# 드라이버는 jsonb를 문자열로 내주므로 연결마다 해석 코덱을 걸어야 목록과 사전으로 읽힌다.
async def _decode_json(connection: Any) -> None:
    await connection.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class LedgerPoolProvider:
    """원장 연결 풀을 처음 필요한 순간에 열고 그 뒤로는 같은 풀을 내준다."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: LedgerPool | None = None
        self._lock = asyncio.Lock()

    async def pool(self) -> LedgerPool:
        """원장 뷰에 연결되는 연결 풀을 돌려준다."""
        async with self._lock:
            pool = self._pool
            if pool is None:
                pool = await asyncpg.create_pool(
                    self._dsn, min_size=MIN_POOL_SIZE, max_size=MAX_POOL_SIZE, init=_decode_json
                )
                if pool is None:
                    raise RuntimeError("원장 연결 풀을 열지 못했다")
                self._pool = pool
            return pool

    async def close(self) -> None:
        """열린 적이 있으면 연결 풀을 닫는다."""
        async with self._lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None


class UniqueViolation(Exception):
    """유일 제약이 이미 찬 자리를 다시 채우려는 문장을 막았다."""


class LedgerSql(Protocol):
    """조건부 갱신 한 문장을 실행하고 갱신된 행을 내는 연결 표면이다."""

    async def fetch(self, sql: str, *args: Any) -> list[SqlRow]:
        """문장 하나를 실행하고 돌아온 행을 사전으로 낸다."""
        ...

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """여러 문장이 함께 남거나 함께 사라지는 경계를 연다."""
        ...


class AsyncpgSql:
    """asyncpg 연결 하나를 문장 실행 표면으로 감싸고 유일 제약 위반만 결과로 바꿔 낸다."""

    def __init__(self, connection: LedgerConnection) -> None:
        self._connection = connection

    async def fetch(self, sql: str, *args: Any) -> list[SqlRow]:
        """문장 하나를 실행하고 돌아온 행을 사전으로 낸다."""
        try:
            rows = await self._connection.fetch(sql, *args)
        except asyncpg.UniqueViolationError as error:
            raise UniqueViolation(str(error)) from error
        return [dict(row) for row in rows]

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """여러 문장이 함께 남거나 함께 사라지는 경계를 연다."""
        return _asyncpg_transaction(self._connection)


@asynccontextmanager
async def _asyncpg_transaction(connection: LedgerConnection) -> AsyncIterator[None]:
    async with connection.transaction():
        yield


@asynccontextmanager
async def acquire_sql(pool: LedgerPool) -> AsyncIterator[LedgerSql]:
    """풀에서 연결 하나를 빌려 문장 실행 표면으로 감싼 뒤 반납한다."""
    async with pool.acquire() as connection:
        yield AsyncpgSql(connection)


class SqlSource(Protocol):
    """호출 한 건이 쓸 문장 실행 표면을 빌려 주는 창구다."""

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """문장 실행 표면 하나를 빌리고 쓰임이 끝나면 반납한다."""
        ...


class PooledSql:
    """연결 풀에서 호출 한 건이 쓸 문장 실행 표면을 빌려 준다."""

    def __init__(self, provider: LedgerPoolProvider) -> None:
        self._provider = provider

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """문장 실행 표면 하나를 빌리고 쓰임이 끝나면 반납한다."""
        return self._connect()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[LedgerSql]:
        async with acquire_sql(await self._provider.pool()) as sql:
            yield sql
