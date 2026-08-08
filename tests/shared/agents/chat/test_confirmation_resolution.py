"""확인 해소 객체가 실행·전이·이어 말할 턴·알림을 한 자리에서 소유하는지 검증한다."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.support.chat_surface import (
    NOW,
    RecordingDispatch,
    RecordingExecutor,
    RecordingUpdates,
    SilentWatch,
    seed_execution,
    seed_pending_tool,
    seed_thread,
)
from tracer_agent.shared.agents.chat.intake.follow_up import follow_up_client_request_id
from tracer_agent.shared.agents.chat.rejections import ChatRejected
from tracer_agent.shared.agents.chat.surface.ledger import ChatSurfaceLedger
from tracer_agent.shared.agents.chat.surface.resolution import (
    ChatConfirmationResolution,
    ThreadUpdateAnnouncer,
)
from tracer_agent.shared.agents.chat.surface.tool_client import ChatToolFailed
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import UniqueViolation


class _BlockingExecutor(RecordingExecutor):
    """상류 쓰기 도중에 같은 확인의 두 번째 승인이 들어오도록 그 호출을 붙든다."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, user_id: str, tool_name: str, args: dict[str, Any]) -> str:
        sentence = await super().execute(user_id, tool_name, args)
        self.entered.set()
        await self.release.wait()
        return sentence


class _FailingExecutor(RecordingExecutor):
    """상류가 승인된 도구 호출을 거절한다."""

    async def execute(self, user_id: str, tool_name: str, args: dict[str, Any]) -> str:
        await super().execute(user_id, tool_name, args)
        raise ChatToolFailed("propose_task_write answered 500", status=500)


def _store() -> SqliteLedgerSql:
    store = SqliteLedgerSql()
    seed_thread(store)
    seed_pending_tool(store)
    return store


def _resolution(
    store: SqliteLedgerSql,
    executor: RecordingExecutor,
    dispatch: RecordingDispatch,
    updates: RecordingUpdates | None = None,
    watch: SilentWatch | None = None,
) -> ChatConfirmationResolution:
    return ChatConfirmationResolution(
        store,
        executor,
        dispatch,
        ThreadUpdateAnnouncer(ChatSurfaceLedger(store), updates, watch),
    )


async def test_승인은_도구를_먼저_부르고_이어_말할_턴을_세운다() -> None:
    store = _store()
    executor = RecordingExecutor()
    dispatch = RecordingDispatch()

    resolved = await _resolution(store, executor, dispatch).resolve("local", "t1", "c1", "approve", NOW)

    assert executor.calls == [("local", "propose_task_write", {"action": "archive", "taskId": "task-1"})]
    assert resolved.status == "approved"
    assert resolved.result == "Archived task task-1."
    assert resolved.execution is not None
    # 이어 말할 턴은 원장에 서고 그 실행만 워크플로로 나간다.
    assert dispatch.started == [(str(resolved.execution["id"]), "t1")]
    assert store.rows("chat_messages")[0]["role"] == "tool"


async def test_거절은_도구를_부르지_않고_이어_말할_턴도_세우지_않는다() -> None:
    store = _store()
    executor = RecordingExecutor()
    dispatch = RecordingDispatch()

    resolved = await _resolution(store, executor, dispatch).resolve("local", "t1", "c1", "reject", NOW)

    assert executor.calls == []
    assert dispatch.started == []
    assert resolved.status == "rejected"
    assert resolved.execution is None
    assert "rejected" in resolved.result


async def test_아직_문맥을_읽지_않은_턴이_대기하면_줄을_하나_더_세우지_않는다() -> None:
    store = _store()
    seed_execution(store, status="queued")

    resolved = await _resolution(store, RecordingExecutor(), RecordingDispatch()).resolve(
        "local", "t1", "c1", "approve", NOW
    )

    assert resolved.execution is None


async def test_실행_중인_턴은_이_결과를_문맥으로_읽지_못하므로_이어_말할_턴을_세운다() -> None:
    store = _store()
    seed_execution(store, status="running")
    dispatch = RecordingDispatch()

    resolved = await _resolution(store, RecordingExecutor(), dispatch).resolve(
        "local", "t1", "c1", "approve", NOW
    )

    assert resolved.execution is not None
    assert dispatch.started == [(str(resolved.execution["id"]), "t1")]


async def test_같은_확인을_겹쳐_승인해도_상류_쓰기는_한_번만_일어난다() -> None:
    store = _store()
    executor = _BlockingExecutor()
    resolution = _resolution(store, executor, RecordingDispatch())

    first = asyncio.create_task(resolution.resolve("local", "t1", "c1", "approve", NOW))
    await executor.entered.wait()
    second = asyncio.create_task(resolution.resolve("local", "t1", "c1", "approve", NOW))
    # 뒤이은 요청이 자리를 집으려다 거절되기까지 앞선 요청은 상류에 머문다.
    with pytest.raises(ChatRejected) as rejected:
        await second
    executor.release.set()
    await first

    assert rejected.value.status == 409
    assert len(executor.calls) == 1


async def test_상류_쓰기가_실패하면_확인은_다시_승인할_수_있는_자리로_돌아온다() -> None:
    store = _store()

    with pytest.raises(ChatToolFailed):
        await _resolution(store, _FailingExecutor(), RecordingDispatch()).resolve(
            "local", "t1", "c1", "approve", NOW
        )

    assert store.rows("chat_pending_tools")[0]["status"] == "pending"
    assert store.rows("chat_messages") == []


async def test_전이_이후_구간이_실패하면_도구_결과_줄도_함께_사라진다() -> None:
    store = _store()
    # 같은 확인이 세울 이어 말할 턴이 이미 원장에 있으면 그 삽입이 멱등 색인에 걸린다.
    seed_execution(store, follow_up_client_request_id("c1"), status="completed")

    with pytest.raises(UniqueViolation):
        await _resolution(store, RecordingExecutor(), RecordingDispatch()).resolve(
            "local", "t1", "c1", "approve", NOW
        )

    assert store.rows("chat_messages") == []
    assert len(store.rows("chat_executions")) == 1


async def test_해소는_열려_있는_실행_채널로만_알린다() -> None:
    store = _store()
    seed_execution(store, status="running")
    updates = RecordingUpdates()
    watch = SilentWatch()

    await _resolution(store, RecordingExecutor(), RecordingDispatch(), updates, watch).resolve(
        "local", "t1", "c1", "approve", NOW
    )

    assert updates.published == ["e1"]
    assert watch.notified == ["e1"]


async def test_남의_스레드에_걸린_확인은_존재조차_알리지_않는다() -> None:
    store = _store()

    with pytest.raises(ChatRejected) as rejected:
        await _resolution(store, RecordingExecutor(), RecordingDispatch()).resolve(
            "local", "t2", "c1", "approve", NOW
        )

    assert rejected.value.status == 404


async def test_이미_해소된_확인은_다시_해소되지_않는다() -> None:
    store = SqliteLedgerSql()
    seed_thread(store)
    seed_pending_tool(store, status="approved")

    with pytest.raises(ChatRejected) as rejected:
        await _resolution(store, RecordingExecutor(), RecordingDispatch()).resolve(
            "local", "t1", "c1", "approve", NOW
        )

    assert rejected.value.status == 409
