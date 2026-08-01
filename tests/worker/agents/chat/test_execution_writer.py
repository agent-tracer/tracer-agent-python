"""한 턴의 산출물이 실행 전이와 함께 원장에 남거나 함께 사라지는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.brokers import RecordingProducer
from tests.support.contract import wire_contract
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.chat.execution_ledger import ChatExecutionSpend
from tracer_agent.shared.agents.chat.models import EXECUTION_BACKEND
from tracer_agent.shared.agents.runtime.wakeup import UpdatePublisher
from tracer_agent.shared.agents.shared.models import AgentStepDTO
from tracer_agent.shared.workflows.chat_spec import CHAT_EXECUTION_UPDATES_TOPIC
from tracer_agent.worker.agents.chat.execution_writer import ChatExecutionWriter, ChatTurnOutcome

NOW = datetime(2026, 7, 26, 0, 5, tzinfo=UTC)

SPEND = ChatExecutionSpend(
    model_used="claude-haiku-4-5", cost_usd=0.01, num_turns=2, stop_reason="completed", usage={}
)


def thread_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "t1",
        "user_id": "u1",
        "title": "대화",
        "backend": None,
        "created_at": datetime(2026, 7, 26, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 26, tzinfo=UTC),
    }
    return defaults | overrides


def execution_row(status: str) -> dict[str, Any]:
    return {
        "id": "e1",
        "user_id": "u1",
        "thread_id": "t1",
        "user_message_id": "m1",
        "client_request_id": "r1",
        "input_hash": "h1",
        "status": status,
        "created_at": datetime(2026, 7, 26, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 26, tzinfo=UTC),
    }


def outcome(**overrides: Any) -> ChatTurnOutcome:
    defaults: dict[str, Any] = {
        "execution_id": "e1",
        "user_id": "u1",
        "thread_id": "t1",
        "attempt": 1,
        "canceled": False,
        "text": "답",
        "spend": SPEND,
        "steps": [AgentStepDTO(seq=0, role="assistant", content="답")],
        "observation": observation(),
    }
    values = defaults | overrides
    if "observation" not in overrides:
        values["observation"] = observation(executionId=values["execution_id"])
    return ChatTurnOutcome(**values)


def observation(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "executionId": "e1",
        "attemptId": "1",
        "jobId": None,
        "agentName": "chat",
        "backend": "python",
        "modelRequested": "model",
        "modelActual": "model",
        "promptVersion": "1.0.0",
        "toolContractVersion": "1.0.0",
        "status": "succeeded",
        "durationMs": 1,
        "usage": {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0},
        "costUsd": None,
        "landed": True,
        "repairAttempted": False,
        "validation": {"passed": True, "errorCodes": [], "citationPrecision": None, "citationRecall": None},
        "modelCalls": [],
        "toolCalls": [],
    }
    return defaults | overrides


class BrokenSteps:
    """궤적을 적는 문장에서만 실패하는 문장 실행 표면 대역이다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """궤적 문장이면 실패하고 나머지는 감싸는 표면에 넘긴다."""
        if "chat_execution_steps" in sql:
            raise RuntimeError("궤적을 적지 못했다")
        return await self._store.fetch(sql, *args)

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """감싸는 표면의 트랜잭션 경계를 그대로 쓴다."""
        return self._store.transaction()


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    ledger.seed("chat_threads", [thread_row()])
    yield ledger
    ledger.close()


async def test_완료가_메시지와_궤적을_함께_남긴다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("running")])

    assert await ChatExecutionWriter(store).finalize(outcome(), NOW) is True

    execution = store.rows("chat_executions")[0]
    assert execution["status"] == "completed"
    assert execution["assistant_message_id"] == "e1"
    assert [row["content"] for row in store.rows("chat_messages")] == ["답"]
    assert [row["id"] for row in store.rows("chat_execution_steps")] == ["e1:1:0"]


async def test_전이가_막히면_아무것도_남지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("completed")])

    assert await ChatExecutionWriter(store).finalize(outcome(), NOW) is False

    assert store.rows("chat_messages") == []
    assert store.rows("chat_execution_steps") == []


async def test_같은_턴을_다시_적어도_행이_늘지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("canceled")])
    writer = ChatExecutionWriter(store)

    assert (
        await writer.finalize(outcome(canceled=True, observation=observation(status="cancelled")), NOW)
        is True
    )
    assert (
        await writer.finalize(
            outcome(canceled=True, text="다른 답", observation=observation(status="cancelled")), NOW
        )
        is False
    )

    assert len(store.rows("chat_messages")) == 1
    assert len(store.rows("chat_execution_steps")) == 1


async def test_같은_시도에_다른_관측은_전체를_되감는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("running")])
    writer = ChatExecutionWriter(store)
    await writer.record_observation("e1", "u1", 1, observation(), NOW)

    with pytest.raises(ValueError, match="conflicts with immutable attempt"):
        await writer.finalize(outcome(observation=observation(modelActual="other")), NOW)

    assert store.rows("chat_executions")[0]["status"] == "running"
    assert store.rows("chat_messages") == []


async def test_궤적을_적다_실패하면_전이도_되돌아간다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("running")])

    with pytest.raises(RuntimeError, match="궤적을 적지 못했다"):
        await ChatExecutionWriter(BrokenSteps(store)).finalize(outcome(), NOW)

    assert store.rows("chat_executions")[0]["status"] == "running"
    assert store.rows("chat_messages") == []


async def test_적힌_뒤에만_갱신을_알린다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("completed")])
    producer = RecordingProducer()
    wakeup = UpdatePublisher("broker:9092", CHAT_EXECUTION_UPDATES_TOPIC, lambda _brokers: producer)

    assert await ChatExecutionWriter(store, wakeup).finalize(outcome(), NOW) is False
    assert producer.sent == []

    store.seed("chat_executions", [execution_row("running") | {"id": "e2", "client_request_id": "r2"}])
    assert await ChatExecutionWriter(store, wakeup).finalize(outcome(execution_id="e2"), NOW) is True
    assert producer.sent == [(CHAT_EXECUTION_UPDATES_TOPIC, b'{"executionId": "e2"}', b"e2")]


async def test_완료가_스레드의_백엔드와_정렬_기준을_민다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("running")])

    assert await ChatExecutionWriter(store).finalize(outcome(), NOW) is True

    thread = store.rows("chat_threads")[0]
    assert thread["backend"] == EXECUTION_BACKEND
    assert thread["updated_at"] == NOW


async def test_남의_스레드에_적으려_하면_턴_전체가_되감긴다(store: SqliteLedgerSql) -> None:
    store.seed("chat_executions", [execution_row("running")])

    assert await ChatExecutionWriter(store).finalize(outcome(user_id="u2"), NOW) is False

    assert store.rows("chat_executions")[0]["status"] == "running"
    assert store.rows("chat_messages") == []
    assert store.rows("chat_threads")[0]["backend"] is None


def test_실행_갱신_토픽이_계약과_같다() -> None:
    topic = wire_contract("topics.json")["chatExecutionUpdates"]["name"]

    assert topic == CHAT_EXECUTION_UPDATES_TOPIC
