"""액티비티 넷이 실행 원장을 어떤 조건에서 전진시키고 어떤 실패를 다시 태우지 않는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.workflows.chat_spec import (
    STOP_COMPLETED,
    THREAD_BUSY_FAILURE,
    ChatExecutionRequest,
    FailedChatExecution,
    GeneratedChatExecution,
    PreparedChatExecution,
)
from tracer_agent.worker.agents.chat.checkpoint import ChatCheckpointProvider
from tracer_agent.worker.workflows.chat_activities import ChatExecutionActivities
from tracer_agent.worker.workflows.envelope import ChatExecutionEnvelope

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


class SingleSqlSource:
    """메모리 원장 하나를 액티비티마다 다시 빌려 주는 문장 실행 창구다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """같은 메모리 원장을 그대로 빌려 준다."""
        return self._connect()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[LedgerSql]:
        yield self._store


def thread_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "t1",
        "user_id": "u1",
        "title": "대화",
        "backend": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return defaults | overrides


def execution_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "e1",
        "user_id": "u1",
        "thread_id": "t1",
        "user_message_id": "m1",
        "client_request_id": "r1",
        "input_hash": "h1",
        "status": "queued",
        "language": "ko",
        "model": "claude-haiku-4-5",
        "created_at": NOW,
        "updated_at": NOW,
    }
    return defaults | overrides


def generated(**overrides: Any) -> GeneratedChatExecution:
    defaults: dict[str, Any] = {
        "execution_id": "e1",
        "thread_id": "t1",
        "user_id": "u1",
        "attempt": 1,
        "canceled": False,
        "text": "답",
        "model_used": "claude-haiku-4-5",
        "stop_reason": STOP_COMPLETED,
        "observation": observation(),
    }
    return GeneratedChatExecution(**(defaults | overrides))


def observation(status: str = "succeeded") -> dict[str, Any]:
    return {
        "executionId": "e1",
        "attemptId": "1",
        "jobId": None,
        "agentName": "chat",
        "backend": "python",
        "modelRequested": "model",
        "modelActual": "model",
        "promptVersion": "1.0.0",
        "promptContentHash": "sha256:test",
        "toolContractVersion": "1.0.0",
        "status": status,
        "durationMs": 1,
        "usage": {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0},
        "costUsd": None,
        "landed": True,
        "repairAttempted": False,
        "validation": {
            "passed": status == "succeeded",
            "errorCodes": [] if status == "succeeded" else [status],
        },
        "modelCalls": [],
        "toolCalls": [],
    }


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    ledger.seed("chat_threads", [thread_row()])
    yield ledger
    ledger.close()


class StubEnvelopes:
    """서버가 내줄 봉투 조각을 그대로 돌려주는 창구다."""

    def __init__(self, fields: dict[str, Any] | None = None) -> None:
        self._fields = fields or {}
        self.issued: list[tuple[str, int]] = []

    async def issue(self, execution_id: str, attempt: int) -> ChatExecutionEnvelope:
        """무엇을 몇 번째 시도로 물었는지 기억하고 미리 정한 조각을 낸다."""
        self.issued.append((execution_id, attempt))
        return ChatExecutionEnvelope(fields=dict(self._fields), draft_token_hash="hash")


@pytest.fixture
def activities(store: SqliteLedgerSql) -> ChatExecutionActivities:
    return ChatExecutionActivities(
        SingleSqlSource(store),
        httpx.AsyncClient(),
        ChatCheckpointProvider("postgresql://unused"),
        StubEnvelopes(),
    )


async def test_준비가_대기_실행을_집고_원장이_든_사실을_낸다(
    store: SqliteLedgerSql, activities: ChatExecutionActivities
) -> None:
    store.seed("chat_executions", [execution_row()])

    prepared = await activities.prepare(ChatExecutionRequest("e1", "t1"))

    assert prepared == PreparedChatExecution("e1", "t1", "u1", "ko", "claude-haiku-4-5")
    assert store.rows("chat_executions")[0]["status"] == "running"


async def test_잠긴_스레드는_이_실행의_실패가_아니다(
    store: SqliteLedgerSql, activities: ChatExecutionActivities
) -> None:
    store.seed(
        "chat_executions",
        [execution_row(id="e0", client_request_id="r0", status="running", updated_at=datetime.now(UTC))],
    )
    store.seed("chat_executions", [execution_row()])

    with pytest.raises(ApplicationError) as raised:
        await activities.prepare(ChatExecutionRequest("e1", "t1"))

    assert raised.value.type == THREAD_BUSY_FAILURE
    assert store.rows("chat_executions")[1]["status"] == "queued"


async def test_갱신이_끊긴_running을_되돌리고_다시_집는다(
    store: SqliteLedgerSql, activities: ChatExecutionActivities
) -> None:
    stale = datetime(2020, 1, 1, tzinfo=UTC)
    store.seed(
        "chat_executions",
        [execution_row(id="e0", client_request_id="r0", status="running", updated_at=stale)],
    )
    store.seed("chat_executions", [execution_row()])

    await activities.prepare(ChatExecutionRequest("e1", "t1"))

    assert [row["status"] for row in store.rows("chat_executions")] == ["queued", "running"]


async def test_없는_실행은_다시_태우지_않는다(activities: ChatExecutionActivities) -> None:
    with pytest.raises(ApplicationError) as raised:
        await activities.prepare(ChatExecutionRequest("없다", "t1"))

    assert raised.value.non_retryable is True


async def test_종결이_산출물과_스레드_갱신을_함께_적는다(
    store: SqliteLedgerSql, activities: ChatExecutionActivities
) -> None:
    store.seed("chat_executions", [execution_row(status="running")])

    await activities.finalize(generated())

    assert store.rows("chat_executions")[0]["status"] == "completed"
    assert store.rows("chat_threads")[0]["backend"] == "python"


async def test_빈_취소_턴은_실행을_그대로_둔다(
    store: SqliteLedgerSql, activities: ChatExecutionActivities
) -> None:
    store.seed("chat_executions", [execution_row(status="canceled")])

    await activities.finalize(generated(canceled=True, text="  "))

    assert store.rows("chat_executions")[0]["assistant_message_id"] is None
    assert store.rows("chat_messages") == []


async def test_취소된_턴의_산출물은_남는다(
    store: SqliteLedgerSql, activities: ChatExecutionActivities
) -> None:
    store.seed("chat_executions", [execution_row(status="canceled")])

    await activities.finalize(generated(canceled=True, text="여기까지", observation=observation("cancelled")))

    assert [row["content"] for row in store.rows("chat_messages")] == ["여기까지"]


async def test_실패가_살아_있는_실행만_닫는다(
    store: SqliteLedgerSql, activities: ChatExecutionActivities
) -> None:
    store.seed("chat_executions", [execution_row(status="running")])
    store.seed("chat_executions", [execution_row(id="e2", client_request_id="r2", status="completed")])

    store.seed(
        "agent_run_observations",
        [
            {
                "execution_id": "e1",
                "attempt_id": "1",
                "user_id": "u1",
                "agent_name": "chat",
                "backend": "python",
                "model_requested": "model",
                "prompt_version": "1.0.0",
                "prompt_content_hash": "sha256:test",
                "tool_contract_version": "1.0.0",
                "status": "failed",
                "duration_ms": 1,
                "usage": {},
                "landed": False,
                "repair_attempted": False,
                "validation": {},
                "model_calls": [],
                "tool_calls": [],
                "created_at": NOW,
            }
        ],
    )
    await activities.fail(FailedChatExecution("e1", "끊겼다"))
    await activities.fail(FailedChatExecution("e2", "끊겼다"))

    assert [row["status"] for row in store.rows("chat_executions")] == ["failed", "completed"]


async def test_계약을_벗어난_봉투는_다시_태우지_않는다(activities: ChatExecutionActivities) -> None:
    prepared = PreparedChatExecution("e1", "t1", "u1", "ko")

    with pytest.raises(ApplicationError) as raised:
        await ActivityEnvironment().run(activities.generate, prepared)

    assert raised.value.non_retryable is True
