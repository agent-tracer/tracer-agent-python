"""LangGraph 채팅 실행 상태를 PostgreSQL에 보존하는 체크포인터 수명을 관리한다."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


class ChatCheckpointProvider:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None
        self._saver: AsyncPostgresSaver | None = None
        self._lock = asyncio.Lock()

    async def saver(self) -> AsyncPostgresSaver:
        async with self._lock:
            if self._saver is None:
                context = AsyncPostgresSaver.from_conn_string(self._dsn)
                saver = await context.__aenter__()
                self._context = context
                self._saver = saver
            return self._saver

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.__aexit__(None, None, None)
                self._context = None
                self._saver = None
