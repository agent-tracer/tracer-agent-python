"""열린 연결이 실행 스냅샷을 이어서 전송하고 종결에서 닫는지 검증한다."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.support.chat_surface import (
    NOW,
    SingleSql,
    seed_execution,
    seed_pending_tool,
    seed_thread,
)
from tracer_agent.shared.agents.chat.execution_ledger import ChatExecutionLedger
from tracer_agent.shared.agents.chat.surface.stream import frames, watch_chat_execution
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql

THREADS = "/api/agent/chat/threads"


def _frames(body: str) -> list[dict[str, object]]:
    return [json.loads(chunk.split("data: ", 1)[1]) for chunk in body.split("\n\n") if "data: " in chunk]


class WakingWatch:
    """열린 연결의 알림을 시험이 직접 켜고 구독을 놓았는지 관측한다."""

    def __init__(self) -> None:
        self.released = False
        self._listener: Callable[[], None] | None = None

    def subscribe(self, _execution_id: str, listener: Callable[[], None]) -> Callable[[], None]:
        """이 실행의 알림을 맡아 두고 그만 듣는 방법을 낸다."""
        self._listener = listener
        return self._release

    def wake(self) -> None:
        """맡아 둔 알림을 켠다."""
        assert self._listener is not None
        self._listener()

    def notify(self, _execution_id: str) -> None:
        """이 프로세스가 스스로 알리는 자리이며 시험은 wake 를 직접 쓴다."""
        self.wake()

    def _release(self) -> None:
        self.released = True


class OrderedSource:
    """정본을 읽은 순간을 구독한 순간과 같은 줄에 적는다."""

    def __init__(self, inner: SingleSql, events: list[str]) -> None:
        self._inner = inner
        self._events = events

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """정본을 읽는다는 사실을 적고 원장을 그대로 빌려 준다."""
        self._events.append("read")
        return self._inner.connect()


class OrderedWatch(WakingWatch):
    """구독한 순간을 정본을 읽은 순간과 같은 줄에 적는다."""

    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def subscribe(self, execution_id: str, listener: Callable[[], None]) -> Callable[[], None]:
        """구독한다는 사실을 적고 알림을 맡아 둔다."""
        self._events.append("subscribe")
        return super().subscribe(execution_id, listener)


class Test실행_스냅샷_스트림:
    def test_종결_상태의_스냅샷을_한_번_보내고_연결을_닫는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(
            store,
            "e1",
            status="completed",
            draft_text="끝난 답변",
            draft_seq=4,
            updated_at=NOW + timedelta(seconds=2),
        )
        seed_pending_tool(store)

        with client.stream("GET", f"{THREADS}/t1/executions/e1/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())

        frames = _frames(body)
        assert len(frames) == 1
        assert body.splitlines()[0] == "event: snapshot"
        assert frames[0]["execution"]["status"] == "completed"  # type: ignore[index]
        assert frames[0]["confirmations"] == [
            {"id": "c1", "toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}}
        ]

    def test_남의_실행의_연결은_열지_않는다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", thread_id="other", status="completed")

        res = client.get(f"{THREADS}/t1/executions/e1/events")

        assert res.status_code == 404
        assert res.json()["error"]["code"] == "not_found"

    def test_해소된_대기_도구는_프레임에_싣지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(store, "e1", status="failed")
        seed_pending_tool(store, "c1", status="approved")

        with client.stream("GET", f"{THREADS}/t1/executions/e1/events") as response:
            body = "".join(response.iter_text())

        assert _frames(body)[0]["confirmations"] == []


class Test끊김과_취소와_재생:
    def test_지난_사건_식별자를_실어도_정본부터_내고_식별자_줄을_쓰지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(store, "e1", status="completed")

        with client.stream(
            "GET", f"{THREADS}/t1/executions/e1/events", headers={"Last-Event-ID": "7"}
        ) as response:
            body = "".join(response.iter_text())

        assert "id:" not in body
        assert _frames(body)[0]["execution"]["status"] == "completed"  # type: ignore[index]

    async def test_도는_실행이_종결로_바뀌면_그_스냅샷을_내고_닫는다(self, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", status="running")
        watch = WakingWatch()

        stream = frames(watch, SingleSql(store), "local", "t1", "e1")
        assert '"status": "running"' in await anext(stream)
        await ChatExecutionLedger(store).cancel_active("e1", NOW)
        watch.wake()
        assert '"status": "canceled"' in await anext(stream)

        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert watch.released

    async def test_첫_프레임은_구독을_놓은_뒤에_읽은_정본이다(self, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", status="completed")
        events: list[str] = []
        watch = OrderedWatch(events)

        response = await watch_chat_execution(
            "t1", "e1", OrderedSource(SingleSql(store), events), "local", watch
        )
        sent = [chunk async for chunk in response.body_iterator]

        assert len(sent) == 1
        # 구독보다 먼저 읽은 정본을 첫 프레임으로 쓰면 그 사이에 온 갱신을 재전송 주기까지 놓친다.
        assert events == ["read", "subscribe", "read"]

    async def test_연결이_끊기면_구독을_놓는다(self, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", status="running")
        watch = WakingWatch()

        stream = frames(watch, SingleSql(store), "local", "t1", "e1")
        await anext(stream)
        await stream.aclose()

        assert watch.released

    async def test_정본이_그대로면_신호가_와도_프레임을_내지_않는다(self, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", status="running")
        watch = WakingWatch()

        stream = frames(watch, SingleSql(store), "local", "t1", "e1")
        await anext(stream)
        watch.wake()

        # 정본이 그대로면 신호가 와도 낼 것이 없어 다음 프레임은 주기 재전송까지 오지 않는다.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(stream), 0.2)
        await stream.aclose()
