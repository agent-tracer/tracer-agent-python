"""LangGraph 실행 상태를 PostgreSQL에 보존하는 체크포인터 수명을 관리한다."""

from __future__ import annotations

import asyncio
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from ....shared.config import CHECKPOINT_SCHEMA
from .serde import graph_serde

_log = logging.getLogger(__name__)

# 한 워커가 동시에 소비하는 실행 수만큼 체크포인트 왕복이 겹치므로 연결 하나로 직렬화하지 않는다.
CHECKPOINT_POOL_MAX_SIZE = 8
# 세이버는 자동 커밋과 dict 행과 준비 없는 문장을 전제로 질의를 쓴다.
_CONNECTION_KWARGS = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}


class GraphCheckpointProvider:
    """한 워커 프로세스가 공유하는 Postgres 세이버 하나를 지연 생성해 가진다."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._saver: AsyncPostgresSaver | None = None
        self._lock = asyncio.Lock()

    async def saver(self) -> AsyncPostgresSaver:
        async with self._lock:
            if self._saver is None:
                await self._create_schema()
                pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
                    conninfo=self._dsn,
                    max_size=CHECKPOINT_POOL_MAX_SIZE,
                    kwargs=_CONNECTION_KWARGS,
                    open=False,
                )
                await pool.open(wait=True)
                saver = AsyncPostgresSaver(pool, serde=graph_serde())
                await saver.setup()
                self._pool = pool
                self._saver = saver
            return self._saver

    async def _create_schema(self) -> None:
        # 체크포인터는 search_path가 가리키는 스키마에 표를 만들 뿐 그 스키마를 만들지 않는다.
        async with await AsyncConnection.connect(self._dsn, autocommit=True) as connection:
            await connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{CHECKPOINT_SCHEMA}"')

    async def forget(self, thread_id: str) -> None:
        """끝난 실행의 체크포인트를 지워 재개용 스냅숏이 원장처럼 쌓이지 않게 한다."""
        if self._saver is None:
            return
        try:
            await self._saver.adelete_thread(thread_id)
        except Exception as unreachable:
            # 지우지 못해도 그 실행의 답은 이미 나갔으므로 종결을 실패로 만들지 않는다.
            _log.warning("checkpoint thread %s was not deleted: %s", thread_id, unreachable)

    async def close(self) -> None:
        async with self._lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
                self._saver = None
