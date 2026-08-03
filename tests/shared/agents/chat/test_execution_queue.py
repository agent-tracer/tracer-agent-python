"""스레드의 대기 줄을 원장이 소유하며 자기 축의 다음 실행 하나만 내는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tracer_agent.shared.agents.chat.execution_ledger import ChatExecutionLedger
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def execution_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "e1",
        "user_id": "u1",
        "thread_id": "t1",
        "replay_anchor_message_id": "m1",
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


async def test_대기_줄이_비면_아무것도_내지_않는다(store: SqliteLedgerSql) -> None:
    assert await ChatExecutionLedger(store).next_queued_in_thread("t1") is None


async def test_접수가_이른_실행을_먼저_낸다(store: SqliteLedgerSql) -> None:
    store.seed(
        "chat_executions",
        [
            execution_row(id="e2", client_request_id="r2", created_at=datetime(2026, 8, 2, 1, tzinfo=UTC)),
            execution_row(id="e1", client_request_id="r1"),
        ],
    )

    assert await ChatExecutionLedger(store).next_queued_in_thread("t1") == "e1"


async def test_다른_축이_접수한_실행은_내지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row(requested_backend="ts")])

    assert await ChatExecutionLedger(store).next_queued_in_thread("t1") is None


async def test_다른_스레드의_실행은_내지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row(thread_id="t2")])

    assert await ChatExecutionLedger(store).next_queued_in_thread("t1") is None


async def test_이미_돌고_있는_실행은_내지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row(status="running")])

    assert await ChatExecutionLedger(store).next_queued_in_thread("t1") is None
