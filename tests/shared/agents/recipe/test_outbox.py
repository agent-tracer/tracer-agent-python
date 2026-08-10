"""색인 아웃박스의 배출과 실패 기록과 잠금 판정을 검증한다(색인 없음)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime

import pytest

from tests.support.recipes import OTHER_AXIS, RecordingSearchIndex, seed_outbox
from tracer_agent.shared.agents.recipe.index import (
    recipe_document_id,
    recipes_index_alias,
    search_outbox_batch_size,
)
from tracer_agent.shared.agents.recipe.outbox import (
    INDEX_WRITE_FAILED,
    SearchOutboxDrain,
)
from tracer_agent.shared.agents.recipe.store import RecipeStore, SearchOutboxStore
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


class OpenDrain:
    """잠금을 언제나 얻는 대역이며 이 대역은 replica 사이의 경쟁을 재현하지 않는다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    async def with_lock(self, work: Callable[[RecipeStore, SearchOutboxStore], Awaitable[int]]) -> int | None:
        """잠금을 얻은 것으로 보고 저장소 묶음을 연다."""
        return await work(RecipeStore(self._store), SearchOutboxStore(self._store))


class LockedDrain:
    """다른 replica 가 이미 잠금을 쥔 상태를 흉내 낸다."""

    async def with_lock(
        self, _work: Callable[[RecipeStore, SearchOutboxStore], Awaitable[int]]
    ) -> int | None:
        """잠금을 얻지 못했다고 알린다."""
        return None


def _seed_recipe(store: SqliteLedgerSql, recipe_id: str, *, deleted: bool = False) -> None:
    store.seed(
        "recipes",
        [
            {
                "id": recipe_id,
                "user_id": "user-1",
                "status": "active",
                "title": "제목",
                "intent": "의도",
                "description": "설명",
                "summary_md": "- 절차",
                "request": "요청",
                "rev": 1,
                "touched_files": [{"path": "docs/db.md", "role": "read"}],
                "created_at": "2026-01-01T00:00:00.000000",
                "updated_at": "2026-01-01T00:00:00.000000",
                "deleted_at": "2026-01-01T00:00:00.000000" if deleted else None,
            }
        ],
    )


async def _enqueue(store: SqliteLedgerSql, row_id: str, recipe_id: str) -> None:
    await SearchOutboxStore(store).enqueue(row_id, "user-1", recipe_id, NOW)


async def test_원장에_있는_레시피는_색인에_문서를_덮어쓴다(store: SqliteLedgerSql) -> None:
    _seed_recipe(store, "r1")
    await _enqueue(store, "o1", "r1")
    index = RecordingSearchIndex()

    drained = await SearchOutboxDrain(OpenDrain(store), index).run_once()

    assert drained == 1
    alias, document_id, document = index.indexed[0]
    assert (alias, document_id) == (recipes_index_alias(), recipe_document_id("r1"))
    assert document["touchedFiles"] == ["docs/db.md"]
    assert store.rows("search_outbox") == []


async def test_적재한_행에_자기_축을_적는다(store: SqliteLedgerSql) -> None:
    await _enqueue(store, "o1", "r1")

    assert store.rows("search_outbox")[0]["backend"] == AGENT_BACKEND


async def test_상대_축이_적재한_행은_가져가지_않는다(store: SqliteLedgerSql) -> None:
    # 축을 거르지 않으면 이 축이 상대 축의 행을 지워 상대 축의 색인 문서가 갱신되지 않는다.
    _seed_recipe(store, "r1")
    seed_outbox(store, "o-ts", "r1", backend=OTHER_AXIS)
    index = RecordingSearchIndex()

    drained = await SearchOutboxDrain(OpenDrain(store), index).run_once()

    assert drained == 0
    assert index.indexed == []
    assert [row["id"] for row in store.rows("search_outbox")] == ["o-ts"]


async def test_배출_한_번이_계약이_적은_수를_넘겨_읽지_않는다(store: SqliteLedgerSql) -> None:
    limit = search_outbox_batch_size()
    for index in range(limit + 1):
        _seed_recipe(store, f"r{index}")
        seed_outbox(
            store,
            f"o{index}",
            f"r{index}",
            created_at=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}.000000",
        )

    drained = await SearchOutboxDrain(OpenDrain(store), RecordingSearchIndex()).run_once()

    assert drained == limit


async def test_조회에_잡히지_않는_대상은_색인에서_지운다(store: SqliteLedgerSql) -> None:
    _seed_recipe(store, "r1", deleted=True)
    await _enqueue(store, "o1", "r1")
    index = RecordingSearchIndex()

    await SearchOutboxDrain(OpenDrain(store), index).run_once()

    assert index.deleted == [(recipes_index_alias(), recipe_document_id("r1"))]
    assert store.rows("search_outbox") == []


async def test_색인이_받지_않으면_시도를_올리고_행을_남긴다(store: SqliteLedgerSql) -> None:
    _seed_recipe(store, "r1")
    await _enqueue(store, "o1", "r1")
    index = RecordingSearchIndex(fail_on={recipe_document_id("r1")})

    drained = await SearchOutboxDrain(OpenDrain(store), index).run_once()

    assert drained == 0
    row = store.rows("search_outbox")[0]
    assert row["attempts"] == 1
    assert row["last_error"] == INDEX_WRITE_FAILED


async def test_실패한_행은_다음_주기가_다시_가져간다(store: SqliteLedgerSql) -> None:
    _seed_recipe(store, "r1")
    await _enqueue(store, "o1", "r1")
    index = RecordingSearchIndex(fail_on={recipe_document_id("r1")})
    drain = SearchOutboxDrain(OpenDrain(store), index)
    await drain.run_once()

    index.fail_on.clear()
    drained = await drain.run_once()

    assert drained == 1
    assert store.rows("search_outbox") == []


async def test_한_행이_실패해도_같은_배치의_다른_행은_배출한다(store: SqliteLedgerSql) -> None:
    _seed_recipe(store, "r1")
    _seed_recipe(store, "r2")
    await _enqueue(store, "o1", "r1")
    await _enqueue(store, "o2", "r2")

    drained = await SearchOutboxDrain(
        OpenDrain(store), RecordingSearchIndex(fail_on={recipe_document_id("r1")})
    ).run_once()

    assert drained == 1
    assert [row["id"] for row in store.rows("search_outbox")] == ["o1"]


async def test_잠금을_얻지_못하면_아무것도_배출하지_않는다(store: SqliteLedgerSql) -> None:
    _seed_recipe(store, "r1")
    await _enqueue(store, "o1", "r1")
    index = RecordingSearchIndex()

    drained = await SearchOutboxDrain(LockedDrain(), index).run_once()

    assert drained == 0
    assert index.indexed == []
    assert len(store.rows("search_outbox")) == 1
