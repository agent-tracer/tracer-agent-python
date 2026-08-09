"""태스크 제목 조회와 보관 요청과 배출 잠금이 계약이 정한 자리로 나가는지 검증한다."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tests.support.contract import conformance_case
from tracer_agent.shared.agents.cleanup.archiver import TracerTaskArchiver
from tracer_agent.shared.agents.recipe.outbox import DRAIN_LOCK_KEY, LedgerSearchOutboxDrain
from tracer_agent.shared.agents.recipe.store import RecipeStore, SearchOutboxStore
from tracer_agent.shared.agents.recipe.tasks import MAX_IDS_PER_CALL, TracerTaskReader
from tracer_agent.shared.agents.runtime.ledger import LedgerSql, SqlRow
from tracer_agent.shared.agents.shared.tracer_window import HttpTracerWindow, UpstreamRejected

_TASKS_CASE = conformance_case("tracer.tasks")
_ARCHIVE_CASE = conformance_case("cleanup.archive")

NOW = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)


def _window(handler: Any) -> tuple[HttpTracerWindow, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return HttpTracerWindow(client, "http://tracer-api"), seen


def _ok(items: list[dict[str, Any]]) -> Any:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "data": {"items": items}})

    return handle


def test_한_번에_묻는_식별자의_상한이_계약과_같다() -> None:
    assert _TASKS_CASE["bound"]["maxIds"] == MAX_IDS_PER_CALL


async def test_인용된_식별자를_한_번에_묻는다() -> None:
    window, seen = _window(_ok([{"id": "task-1", "title": "첫 태스크"}]))

    titles = await TracerTaskReader(window).find_titles_by_ids("user-1", ["task-1", "task-2"])

    assert titles == {"task-1": "첫 태스크"}
    assert len(seen) == 1
    assert seen[0].url.path == _TASKS_CASE["window"]["path"]
    assert seen[0].url.params["ids"] == "task-1,task-2"


async def test_상한을_넘는_식별자는_나눠_묻는다() -> None:
    window, seen = _window(_ok([]))
    ids = [f"task-{index}" for index in range(MAX_IDS_PER_CALL + 1)]

    await TracerTaskReader(window).find_titles_by_ids("user-1", ids)

    assert [len(request.url.params["ids"].split(",")) for request in seen] == [MAX_IDS_PER_CALL, 1]


async def test_보관은_관측한_시각을_조건으로_싣는다() -> None:
    window, seen = _window(lambda _request: httpx.Response(200, json={"ok": True, "data": None}))

    await TracerTaskArchiver(window).archive("user-1", "task-1", NOW)

    assert seen[0].url.path == _ARCHIVE_CASE["surfaces"]["archive"]["path"].replace("{taskId}", "task-1")
    assert json.loads(seen[0].read()) == {"ifNoActivitySince": "2026-01-01T00:01:00.000Z"}


async def test_관측한_시각이_없으면_조건을_비워_싣는다() -> None:
    window, seen = _window(lambda _request: httpx.Response(200, json={"ok": True, "data": None}))

    await TracerTaskArchiver(window).archive("user-1", "task-1", None)

    assert json.loads(seen[0].read()) == {"ifNoActivitySince": None}


async def test_추적의_거절은_상태와_코드를_그대로_올린다() -> None:
    rejection = _ARCHIVE_CASE["rejection"]

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            rejection["status"],
            json={"ok": False, "error": {"code": rejection["code"], "message": rejection["message"]}},
        )

    window, _ = _window(handle)

    with pytest.raises(UpstreamRejected) as refused:
        await TracerTaskArchiver(window).archive("user-1", "task-1", NOW)

    assert (refused.value.status, refused.value.code) == (rejection["status"], rejection["code"])


class RecordingSql(LedgerSql):
    """실행한 문장과 인자를 적어 두고 잠금 판정만 정해 주는 원장 대역이다."""

    def __init__(self, *, locked: bool) -> None:
        self.locked = locked
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[SqlRow]:
        """실행한 문장을 적고 잠금 판정만 답한다."""
        self.statements.append((sql, args))
        return [{"locked": self.locked}] if "advisory" in sql else []

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """경계를 열지만 되돌림은 재현하지 않는다."""
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        yield


class OneSql:
    """시험이 세운 원장 대역 하나를 빌려 준다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """같은 대역을 그대로 내준다."""
        return self._connect()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[LedgerSql]:
        yield self._sql


async def test_배출은_자문_잠금을_얻은_뒤에만_저장소를_연다() -> None:
    sql = RecordingSql(locked=True)
    opened: list[int] = []

    async def work(_recipes: RecipeStore, _outbox: SearchOutboxStore) -> int:
        opened.append(1)
        return 0

    assert await LedgerSearchOutboxDrain(OneSql(sql)).with_lock(work) == 0
    assert opened == [1]
    assert sql.statements[0][1] == (DRAIN_LOCK_KEY,)


async def test_잠금을_얻지_못하면_저장소를_열지_않는다() -> None:
    sql = RecordingSql(locked=False)
    opened: list[int] = []

    async def work(_recipes: RecipeStore, _outbox: SearchOutboxStore) -> int:
        opened.append(1)
        return 0

    assert await LedgerSearchOutboxDrain(OneSql(sql)).with_lock(work) is None
    assert opened == []
