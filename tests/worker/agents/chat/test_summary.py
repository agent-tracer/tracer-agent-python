"""문턱을 넘은 스레드의 오래된 메시지가 요약으로 접혀 원장에 남는지 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.support.chat_surface import SingleSql
from tracer_agent.shared.agents.chat.summary_spec import chat_summary_spec
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql, LedgerUnavailable
from tracer_agent.worker.agents.chat.summary import ChatSummaryProjection

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
CREATED = datetime(2026, 8, 9, tzinfo=UTC)

SPEC = chat_summary_spec()
LEDGER_WAIT_S = 2.0


class RecordingSummarizer:
    """부른 프롬프트를 기록하고 정해진 본문을 내는 요약 창구 대역이다."""

    def __init__(self, text: str = "접은 요약") -> None:
        self.prompts: list[str] = []
        self._text = text

    async def summarize(self, prompt: str) -> str:
        """요청받은 프롬프트를 적고 준비한 본문을 낸다."""
        self.prompts.append(prompt)
        return self._text


class BrokenSummarizer:
    """요약 호출이 언제나 실패하는 창구 대역이다."""

    async def summarize(self, _prompt: str) -> str:
        """이 대역은 본문을 내지 않고 호출 실패를 낸다."""
        raise RuntimeError("요약 모델이 응답하지 않았다")


class UnreachableLedger:
    """이력을 읽는 문장에서만 실패하는 문장 실행 표면 대역이다."""

    # 이 대역은 빌릴 자리의 상한을 세지 않으므로 연결을 쥔 채 다시 빌리는 자리를 드러내지 않는다.
    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """감싸는 원장을 그대로 빌려 준다."""
        return self._connect()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[LedgerSql]:
        yield self

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """이력 문장이면 실패하고 나머지는 감싸는 원장에 넘긴다."""
        if "chat_messages" in sql:
            raise LedgerUnavailable("원장 연결을 빌리지 못했다")
        return await self._store.fetch(sql, *args)

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """감싸는 원장의 트랜잭션 경계를 그대로 쓴다."""
        return self._store.transaction()


class BorrowingSummarizer:
    """요약을 만드는 동안 같은 풀에서 원장 연결을 빌리는 창구 대역이다."""

    def __init__(self, source: SingleSql) -> None:
        self._source = source

    async def summarize(self, _prompt: str) -> str:
        """원장 연결 하나를 빌려 조회한 뒤 본문을 낸다."""
        async with self._source.connect() as sql:
            await sql.fetch("SELECT id FROM chat_threads WHERE id = $1", "t1")
        return "빌려 만든 요약"


def thread_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "t1",
        "user_id": "u1",
        "title": "대화",
        "summary": None,
        "backend": None,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return defaults | overrides


def message_rows(contents: list[str], role: str = "user") -> list[dict[str, Any]]:
    return [
        {
            "id": f"m{index}",
            "thread_id": "t1",
            "role": role,
            "content": content,
            "tool_calls": None,
            "tool_call_id": None,
            "created_at": CREATED + timedelta(seconds=index),
        }
        for index, content in enumerate(contents)
    ]


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    ledger.seed("chat_threads", [thread_row()])
    yield ledger
    ledger.close()


def summary_of(store: SqliteLedgerSql) -> str | None:
    value = store.rows("chat_threads")[0]["summary"]
    return None if value is None else str(value)


async def test_문턱에_닿지_않으면_요약을_만들지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_messages", message_rows([f"말{index}" for index in range(SPEC.trigger_messages)]))
    summarizer = RecordingSummarizer()

    assert await ChatSummaryProjection(SingleSql(store), summarizer).project("t1", NOW) is False

    assert summarizer.prompts == []
    assert summary_of(store) is None


async def test_문턱을_넘으면_요약이_원장에_남는다(store: SqliteLedgerSql) -> None:
    contents = [f"말{index}" for index in range(SPEC.trigger_messages + 1)]
    store.seed("chat_messages", message_rows(contents))
    summarizer = RecordingSummarizer()

    assert await ChatSummaryProjection(SingleSql(store), summarizer).project("t1", NOW) is True

    thread = store.rows("chat_threads")[0]
    assert thread["summary"] == "접은 요약"
    assert thread["updated_at"] == NOW


async def test_접는_대상은_재생_창_바깥의_오래된_메시지뿐이다(store: SqliteLedgerSql) -> None:
    contents = [f"말{index}" for index in range(SPEC.trigger_messages + 1)]
    store.seed("chat_messages", message_rows(contents))
    summarizer = RecordingSummarizer()

    await ChatSummaryProjection(SingleSql(store), summarizer).project("t1", NOW)

    prompt = summarizer.prompts[0]
    folded = contents[: len(contents) - SPEC.recent_keep_count]
    assert all(content in prompt for content in folded)
    assert all(content not in prompt for content in contents[len(folded) :])


async def test_접을_것이_없으면_요약을_만들지_않는다(store: SqliteLedgerSql) -> None:
    long_enough = "가" * (SPEC.trigger_chars // SPEC.recent_keep_count + 1)
    store.seed("chat_messages", message_rows([long_enough] * SPEC.recent_keep_count))
    summarizer = RecordingSummarizer()

    assert await ChatSummaryProjection(SingleSql(store), summarizer).project("t1", NOW) is False

    assert summarizer.prompts == []
    assert summary_of(store) is None


async def test_앞선_요약을_이어_붙여_다시_접는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_messages", message_rows([f"말{index}" for index in range(SPEC.trigger_messages + 1)]))
    await ChatSummaryProjection(SingleSql(store), RecordingSummarizer("첫 요약")).project("t1", NOW)
    summarizer = RecordingSummarizer("두 번째 요약")

    await ChatSummaryProjection(SingleSql(store), summarizer).project("t1", NOW)

    assert "첫 요약" in summarizer.prompts[0]
    assert summary_of(store) == "두 번째 요약"


async def test_요약을_만들지_못해도_턴은_실패하지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_messages", message_rows([f"말{index}" for index in range(SPEC.trigger_messages + 1)]))

    assert await ChatSummaryProjection(SingleSql(store), BrokenSummarizer()).project("t1", NOW) is False

    assert summary_of(store) is None


async def test_원장을_읽지_못해도_턴은_실패하지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_messages", message_rows([f"말{index}" for index in range(SPEC.trigger_messages + 1)]))
    summarizer = RecordingSummarizer()

    projected = await ChatSummaryProjection(UnreachableLedger(store), summarizer).project("t1", NOW)

    assert projected is False
    assert summarizer.prompts == []
    assert summary_of(store) is None


async def test_요약을_만드는_동안_원장_연결을_쥐지_않는다(store: SqliteLedgerSql) -> None:
    store.seed("chat_messages", message_rows([f"말{index}" for index in range(SPEC.trigger_messages + 1)]))
    source = SingleSql(store)

    projected = await asyncio.wait_for(
        ChatSummaryProjection(source, BorrowingSummarizer(source)).project("t1", NOW),
        timeout=LEDGER_WAIT_S,
    )

    assert projected is True
    assert summary_of(store) == "빌려 만든 요약"
