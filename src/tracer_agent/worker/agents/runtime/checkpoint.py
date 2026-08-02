"""LangGraph 실행 상태를 PostgreSQL에 보존하는 체크포인터 수명을 관리한다."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection

from ....shared.config import CHECKPOINT_SCHEMA


class GraphCheckpointProvider:
    """한 워커 프로세스가 공유하는 Postgres 세이버 하나를 지연 생성해 쥔다."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None
        self._saver: AsyncPostgresSaver | None = None
        self._lock = asyncio.Lock()

    async def saver(self) -> AsyncPostgresSaver:
        async with self._lock:
            if self._saver is None:
                await self._create_schema()
                context = AsyncPostgresSaver.from_conn_string(self._dsn)
                saver = await context.__aenter__()
                await saver.setup()
                self._context = context
                self._saver = saver
            return self._saver

    async def _create_schema(self) -> None:
        # 체크포인터는 search_path가 가리키는 스키마에 표를 만들 뿐 그 스키마를 만들지 않는다.
        async with await AsyncConnection.connect(self._dsn, autocommit=True) as connection:
            await connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{CHECKPOINT_SCHEMA}"')

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.__aexit__(None, None, None)
                self._context = None
                self._saver = None
