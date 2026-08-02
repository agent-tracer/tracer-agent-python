"""기동 스윕이 주인 잃은 running을 되돌리고 아직 끝나지 않은 턴을 다시 태우는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND
from tracer_agent.worker.workflows.recovery import resume_active_executions

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)
STALE = datetime(2020, 1, 1, tzinfo=UTC)


class SingleSqlSource:
    """메모리 원장 하나를 스윕에 그대로 빌려 준다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """같은 메모리 원장을 그대로 빌려 준다."""
        return self._connect()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[LedgerSql]:
        yield self._store


class RecordingDispatch:
    """스윕이 어떤 실행을 다시 태웠는지 붙잡아 둔다."""

    def __init__(self) -> None:
        self.signaled: list[tuple[str, str]] = []

    async def start(self, execution_id: str, thread_id: str) -> None:
        """신호 대상만 기억한다."""
        self.signaled.append((execution_id, thread_id))

    async def cancel(self, execution_id: str) -> None:
        """스윕은 취소하지 않는다."""


def execution_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "e1",
        "user_id": "u1",
        "thread_id": "t1",
        "user_message_id": "m1",
        "client_request_id": "r1",
        "input_hash": "h1",
        "status": "queued",
        "requested_backend": AGENT_BACKEND,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return defaults | overrides


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


async def test_주인_잃은_running을_되돌리고_다시_태운다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row(status="running", updated_at=STALE)])
    dispatch = RecordingDispatch()

    swept = await resume_active_executions(SingleSqlSource(store), dispatch, NOW)

    assert (swept.recovered, swept.resumed) == (1, 1)
    assert store.rows("chat_executions")[0]["status"] == "queued"
    assert dispatch.signaled == [("e1", "t1")]


async def test_아직_끝나지_않은_턴을_접수_순서대로_다시_태운다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row(id="e1", client_request_id="r1")])
    store.seed(
        "chat_executions",
        [execution_row(id="e2", client_request_id="r2", thread_id="t2", status="running")],
    )
    dispatch = RecordingDispatch()

    swept = await resume_active_executions(SingleSqlSource(store), dispatch, NOW)

    assert swept.resumed == 2
    assert dispatch.signaled == [("e1", "t1"), ("e2", "t2")]


async def test_다른_축이_접수한_턴은_다시_태우지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row(requested_backend="ts")])
    dispatch = RecordingDispatch()

    swept = await resume_active_executions(SingleSqlSource(store), dispatch, NOW)

    assert swept.resumed == 0
    assert dispatch.signaled == []


async def test_다른_축이_접수한_running은_되돌리지_않는다(store: SqliteLedgerSql) -> None:
    store.seed(
        "chat_executions",
        [execution_row(status="running", updated_at=STALE, requested_backend="ts")],
    )
    dispatch = RecordingDispatch()

    swept = await resume_active_executions(SingleSqlSource(store), dispatch, NOW)

    assert swept.recovered == 0
    assert store.rows("chat_executions")[0]["status"] == "running"


async def test_이미_끝난_턴은_다시_태우지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row(status="completed")])
    dispatch = RecordingDispatch()

    swept = await resume_active_executions(SingleSqlSource(store), dispatch, NOW)

    assert swept.resumed == 0
