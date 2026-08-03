"""접수가 소유권과 멱등과 트랜잭션을 지키며 대기 실행을 세우는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.chat.intake.models import PostMessagePayload
from tracer_agent.shared.agents.chat.intake.turn import ChatIntakeRejected, ChatTurnIntake
from tracer_agent.shared.agents.runtime.ledger import UniqueViolation
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND

NOW = datetime(2026, 7, 26, 0, 5, tzinfo=UTC)


class RecordingDispatch:
    """접수가 어떤 실행에 워크플로 신호를 보냈는지 보관해 둔다."""

    def __init__(self) -> None:
        self.signaled: list[tuple[str, str]] = []
        self.canceled: list[str] = []

    async def start(self, execution_id: str, thread_id: str) -> None:
        """신호 대상만 기억한다."""
        self.signaled.append((execution_id, thread_id))

    async def cancel(self, execution_id: str) -> None:
        """취소 대상만 기억한다."""
        self.canceled.append(execution_id)


class LosingInsert:
    """아직 커밋되지 않은 다른 접수가 같은 자리를 차지한 상황을 만든다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store
        self._hidden = True

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """삽입 전 조회에는 아무것도 없다고 답하고 실행 삽입에는 유일 제약 위반을 낸다."""
        if self._hidden and "client_request_id = $3" in sql:
            self._hidden = False
            return []
        if "status IN ('queued', 'running')" in sql:
            return []
        if "INSERT INTO chat_executions" in sql:
            raise UniqueViolation("chat_executions_idempotency")
        return await self._store.fetch(sql, *args)

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """감싸는 저장소의 트랜잭션 경계를 그대로 쓴다."""
        return self._store.transaction()


def thread_row(user_id: str = "u1") -> dict[str, Any]:
    return {
        "id": "t1",
        "user_id": user_id,
        "title": "대화",
        "created_at": datetime(2026, 7, 26, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 26, tzinfo=UTC),
    }


def payload(**overrides: Any) -> PostMessagePayload:
    defaults: dict[str, Any] = {"clientRequestId": "r1", "content": "안녕"}
    return PostMessagePayload.model_validate(defaults | overrides)


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    ledger.seed("chat_threads", [thread_row()])
    yield ledger
    ledger.close()


@pytest.fixture
def dispatch() -> RecordingDispatch:
    return RecordingDispatch()


async def test_접수가_사용자_메시지와_대기_실행을_함께_세운다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    accepted = await ChatTurnIntake(store, dispatch).enqueue("u1", "t1", payload(), NOW)

    message = store.rows("chat_messages")[0]
    execution = store.rows("chat_executions")[0]
    assert message["role"] == "user"
    assert message["content"] == "안녕"
    assert execution["status"] == "queued"
    assert execution["user_message_id"] == message["id"]
    assert execution["attempt"] == 0
    assert execution["draft_text"] == ""
    assert accepted.execution["id"] == execution["id"]
    assert dispatch.signaled == [(execution["id"], "t1")]


async def test_남의_스레드는_없는_것으로_돌려보낸다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    with pytest.raises(ChatIntakeRejected) as rejected:
        await ChatTurnIntake(store, dispatch).enqueue("u2", "t1", payload(), NOW)

    assert (rejected.value.status, rejected.value.code) == (404, "not_found")
    assert store.rows("chat_executions") == []


async def test_없는_스레드도_같은_사유로_돌려보낸다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    with pytest.raises(ChatIntakeRejected) as rejected:
        await ChatTurnIntake(store, dispatch).enqueue("u1", "없는스레드", payload(), NOW)

    assert (rejected.value.status, rejected.value.code) == (404, "not_found")


async def test_같은_요청을_다시_쳐도_실행이_늘지_않는다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    intake = ChatTurnIntake(store, dispatch)

    first = await intake.enqueue("u1", "t1", payload(), NOW)
    second = await intake.enqueue("u1", "t1", payload(), NOW)

    assert second.execution["id"] == first.execution["id"]
    assert second.message["id"] == first.message["id"]
    assert len(store.rows("chat_executions")) == 1
    assert len(store.rows("chat_messages")) == 1


async def test_같은_요청_식별자로_다른_입력을_보내면_거절한다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    intake = ChatTurnIntake(store, dispatch)
    await intake.enqueue("u1", "t1", payload(), NOW)

    with pytest.raises(ChatIntakeRejected) as rejected:
        await intake.enqueue("u1", "t1", payload(content="다른 말"), NOW)

    assert (rejected.value.status, rejected.value.code) == (
        409,
        "chat.execution-idempotency-conflict",
    )


async def test_경쟁에_져도_이긴_접수의_결과를_돌려준다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    winner = await ChatTurnIntake(store, dispatch).enqueue("u1", "t1", payload(), NOW)
    dispatch.signaled.clear()

    losing = ChatTurnIntake(LosingInsert(store), dispatch)
    accepted = await losing.enqueue("u1", "t1", payload(), NOW)

    assert accepted.execution["id"] == winner.execution["id"]
    # 진 접수가 먼저 적은 사용자 메시지는 트랜잭션이 되감아 남지 않는다.
    assert len(store.rows("chat_messages")) == 1
    assert dispatch.signaled == [(winner.execution["id"], "t1")]


async def test_이미_돌고_있는_실행에는_다시_신호하지_않는다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    intake = ChatTurnIntake(store, dispatch)
    accepted = await intake.enqueue("u1", "t1", payload(), NOW)
    await store.fetch(
        "UPDATE chat_executions SET status = 'running' WHERE id = $1 RETURNING id",
        accepted.execution["id"],
    )
    dispatch.signaled.clear()

    await intake.enqueue("u1", "t1", payload(), NOW)

    assert dispatch.signaled == []


async def test_접수는_실행에_자기_축을_적는다(store: SqliteLedgerSql, dispatch: RecordingDispatch) -> None:
    await ChatTurnIntake(store, dispatch).enqueue("u1", "t1", payload(), NOW)

    assert store.rows("chat_executions")[0]["requested_backend"] == AGENT_BACKEND


async def test_진행_중인_턴이_있으면_접수하지_않는다(
    store: SqliteLedgerSql, dispatch: RecordingDispatch
) -> None:
    intake = ChatTurnIntake(store, dispatch)
    await intake.enqueue("u1", "t1", payload(), NOW)

    with pytest.raises(ChatIntakeRejected) as rejected:
        await intake.enqueue("u1", "t1", payload(clientRequestId="r2"), NOW)

    assert (rejected.value.status, rejected.value.code) == (409, "chat.execution-active-conflict")
    assert len(store.rows("chat_executions")) == 1
