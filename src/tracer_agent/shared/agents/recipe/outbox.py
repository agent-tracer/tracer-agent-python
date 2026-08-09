"""색인 반영 요청을 자문 잠금 뒤에서 배출하고 그 배출을 주기로 부른다."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..runtime.ledger import LedgerSql, SqlSource
from .document import SEARCH_OUTBOX_BATCH_SIZE, build_recipe_document
from .index import recipes_index_alias
from .search import SearchIndexWriterPort
from .store import RecipeStore, SearchOutboxStore

_log = logging.getLogger(__name__)

# 색인 배출의 자문 잠금 열쇠이며 이 값을 바꾸면 옛 배출기와 새 배출기가 함께 실행된다.
DRAIN_LOCK_KEY = 8_140_101

# 배출 주기이며 실패한 행은 다음 주기가 다시 가져간다.
DRAIN_INTERVAL_S = 2.0

_TRY_LOCK = "SELECT pg_try_advisory_xact_lock($1) AS locked"

INDEX_WRITE_FAILED = "search index write failed"


class SearchOutboxDrainPort(Protocol):
    """배출이 한 번에 하나만 실행되도록 원장의 자문 잠금 뒤에서 저장소 묶음을 여는 창구다."""

    async def with_lock(self, work: Callable[[RecipeStore, SearchOutboxStore], Awaitable[int]]) -> int | None:
        """잠금을 얻지 못하면 일을 하지 않고 None 을 낸다."""
        ...


class LedgerSearchOutboxDrain:
    """replica 가 여럿이어도 배출이 한 번에 하나만 실행되게 하는 자문 잠금의 소유자다."""

    def __init__(self, source: SqlSource) -> None:
        self._source = source

    async def with_lock(self, work: Callable[[RecipeStore, SearchOutboxStore], Awaitable[int]]) -> int | None:
        """잠금을 얻은 트랜잭션 안에서 저장소 묶음을 열고 배출을 실행한다."""
        async with self._source.connect() as sql, sql.transaction():
            if not await _locked(sql):
                return None
            return await work(RecipeStore(sql), SearchOutboxStore(sql))


async def _locked(sql: LedgerSql) -> bool:
    rows = await sql.fetch(_TRY_LOCK, DRAIN_LOCK_KEY)
    return bool(rows and rows[0]["locked"])


class SearchOutboxDrain:
    """원장 쓰기와 같은 커밋에 적재된 색인 반영 요청을 주기마다 배출한다."""

    def __init__(self, drain: SearchOutboxDrainPort, search_index: SearchIndexWriterPort) -> None:
        self._drain = drain
        self._search_index = search_index

    async def run_once(self) -> int:
        """배출 한 번을 실행하고 색인에 반영한 행의 수를 낸다."""
        drained = await self._drain.with_lock(self._drain_batch)
        if drained is None:
            _log.info("search.outbox_drain.skipped reason=lock_not_acquired")
            return 0
        if drained > 0:
            _log.info("search.outbox_drain.completed count=%d", drained)
        return drained

    async def _drain_batch(self, recipes: RecipeStore, outbox: SearchOutboxStore) -> int:
        rows = await outbox.find_batch(SEARCH_OUTBOX_BATCH_SIZE)
        applied = 0
        for row in rows:
            if await self._apply(str(row["target"]), str(row["target_id"]), int(row["attempts"]), recipes):
                await outbox.delete(str(row["id"]))
                applied += 1
                continue
            await outbox.mark_failed(str(row["id"]), int(row["attempts"]) + 1, INDEX_WRITE_FAILED)
        return applied

    async def _apply(self, target: str, target_id: str, attempts: int, recipes: RecipeStore) -> bool:
        """지운 레시피는 조회에 잡히지 않으므로 없는 것과 지워야 할 것을 구분하지 않는다."""
        try:
            recipe = await recipes.find_by_id(target_id)
            if recipe is None:
                await self._search_index.delete_document(recipes_index_alias(), target_id)
                return True
            await self._search_index.index_document(
                recipes_index_alias(), recipe.id, build_recipe_document(recipe)
            )
        except Exception as failed:
            _log.error(
                "search.outbox_drain.failed target=%s targetId=%s attempts=%d error=%s",
                target,
                target_id,
                attempts,
                failed,
            )
            return False
        return True


class SearchOutboxDrainScheduler:
    """배출을 주기로 부르며 한 주기의 실패가 다음 주기를 멈추지 않게 한다."""

    def __init__(self, drain: SearchOutboxDrain, interval_s: float = DRAIN_INTERVAL_S) -> None:
        self._drain = drain
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """주기 실행을 시작하며 이미 시작했으면 아무것도 하지 않는다."""
        if self._task is None:
            self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        """주기 실행을 멈춘다."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            await self._tick()

    async def _tick(self) -> None:
        try:
            await self._drain.run_once()
        except Exception as crashed:
            _log.error("search.outbox_drain.crashed error=%s", crashed)
