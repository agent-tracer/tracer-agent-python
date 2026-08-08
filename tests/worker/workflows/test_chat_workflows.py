"""워커에 등록될 워크플로 둘의 계약 표면과 스레드가 원장의 대기 줄을 비우는 절차를 검증한다."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

import pytest
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from tracer_agent.shared.workflows.chat_spec import (
    CHAT_ENQUEUE_SIGNAL,
    CHAT_EXECUTION_WORKFLOW,
    CHAT_THREAD_WORKFLOW,
    NEXT_EXECUTION_ACTIVITY,
    PREPARE_ACTIVITY,
    THREAD_BUSY_FAILURE,
    THREAD_BUSY_MAX_ROUNDS,
    THREAD_MAX_CHILDREN,
    ChatExecutionRequest,
    ChatThreadRequest,
)
from tracer_agent.worker.workflows.chat_workflows import ChatExecutionWorkflow, ChatThreadWorkflow


def definition(target: type) -> workflow._Definition:
    """등록 시 Temporal이 읽는 워크플로 정의를 그대로 꺼낸다."""
    found = workflow._Definition.from_class(target)
    assert found is not None
    return found


def test_워크플로_이름이_등록_이름과_같다() -> None:
    assert definition(ChatThreadWorkflow).name == CHAT_THREAD_WORKFLOW
    assert definition(ChatExecutionWorkflow).name == CHAT_EXECUTION_WORKFLOW


def test_스레드_워크플로만_접수_신호를_받는다() -> None:
    assert CHAT_ENQUEUE_SIGNAL in definition(ChatThreadWorkflow).signals
    assert definition(ChatExecutionWorkflow).signals == {}


def test_두_워크플로가_받는_입력이_갈려_있다() -> None:
    assert definition(ChatThreadWorkflow).arg_types == [ChatThreadRequest]
    assert definition(ChatExecutionWorkflow).arg_types == [ChatExecutionRequest]


def test_접수_신호가_실행_식별자_하나만_받는다() -> None:
    signal = definition(ChatThreadWorkflow).signals[CHAT_ENQUEUE_SIGNAL]

    assert signal.arg_types == [str]


class ThreadDriver:
    """원장의 대기 줄과 자식 실행을 대신하며 스레드가 무엇을 묻고 무엇을 태웠는지 보관한다."""

    def __init__(self, queued: Iterable[str], failing: Iterable[str] = ()) -> None:
        self._queued = list(queued)
        self._failing = set(failing)
        self._settled: set[str] = set()
        self._woke = False
        self.asked: list[str] = []
        self.started: list[str] = []

    async def wait_condition(self, _predicate: Any, **_options: Any) -> None:
        """첫 회차만 통과시키고 그다음 유휴에서 스레드를 닫는다."""
        if self._woke:
            raise TimeoutError
        self._woke = True

    async def execute_activity(self, name: str, _arg: Any, **_options: Any) -> str | None:
        """접히지 않은 대기 실행 가운데 앞의 것을 원장처럼 다시 낸다."""
        self.asked.append(name)
        for execution_id in self._queued:
            if execution_id not in self._settled:
                return execution_id
        return None

    async def execute_child_workflow(self, _name: str, request: Any, **_options: Any) -> None:
        """시작한 실행을 기억하고 실패로 지정된 것은 원장에 대기로 남긴다."""
        self.started.append(request.execution_id)
        if request.execution_id in self._failing:
            raise RuntimeError("chat execution did not settle")
        self._settled.add(request.execution_id)


def drive(monkeypatch: pytest.MonkeyPatch, driver: ThreadDriver) -> None:
    monkeypatch.setattr(workflow, "wait_condition", driver.wait_condition)
    monkeypatch.setattr(workflow, "execute_activity", driver.execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", driver.execute_child_workflow)
    monkeypatch.setattr(workflow, "logger", logging.getLogger("test.chat.thread"))


async def test_스레드가_원장에_다음_실행을_묻는다(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = ThreadDriver(queued=[])
    drive(monkeypatch, driver)

    await ChatThreadWorkflow().run(ChatThreadRequest("t1"))

    assert driver.asked == [NEXT_EXECUTION_ACTIVITY]
    assert driver.started == []


async def test_원장이_낸_실행을_접수_순서대로_태운다(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = ThreadDriver(queued=["e1", "e2"])
    drive(monkeypatch, driver)

    await ChatThreadWorkflow().run(ChatThreadRequest("t1"))

    assert driver.started == ["e1", "e2"]


async def test_같은_실행이_연달아_실패하면_회차를_접는다(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = ThreadDriver(queued=["e1", "e2"], failing=["e1"])
    drive(monkeypatch, driver)

    await ChatThreadWorkflow().run(ChatThreadRequest("t1"))

    # 실패한 실행이 원장에 대기로 남아도 같은 회차에서 다시 실행하지 않는다.
    assert driver.started == ["e1"]


class _ContinuedAsNew(Exception):
    """continue_as_new 가 실행을 끊는 자리를 대역이 대신 세운다."""


class ContinueDriver(ThreadDriver):
    """상한을 넘길 만큼 대기 줄을 채우고 새 실행으로 넘어간 요청을 보관한다."""

    def __init__(self, queued: Iterable[str]) -> None:
        super().__init__(queued)
        self.continued: list[Any] = []

    def continue_as_new(self, request: Any) -> None:
        self.continued.append(request)
        raise _ContinuedAsNew


async def test_상한만큼_실행한_스레드는_새_실행으로_넘어간다(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = ContinueDriver(queued=[f"e{index}" for index in range(THREAD_MAX_CHILDREN + 5)])
    drive(monkeypatch, driver)
    monkeypatch.setattr(workflow, "continue_as_new", driver.continue_as_new)

    with pytest.raises(_ContinuedAsNew):
        await ChatThreadWorkflow().run(ChatThreadRequest("t1"))

    assert len(driver.started) == THREAD_MAX_CHILDREN
    assert [request.thread_id for request in driver.continued] == ["t1"]


class BusyDriver:
    """준비를 언제나 잠긴 스레드로 거절하고 실행이 남긴 종결 사유를 보관한다."""

    def __init__(self) -> None:
        self.prepared = 0
        self.failed: list[str] = []

    async def execute_activity(self, name: str, arg: Any, **_options: Any) -> Any:
        if name == PREPARE_ACTIVITY:
            self.prepared += 1
            raise ApplicationError("thread busy", type=THREAD_BUSY_FAILURE)
        self.failed.append(arg.execution_id)
        return None

    async def sleep(self, _seconds: float) -> None:
        return None


async def test_회차를_다_쓰면_잠긴_스레드를_더_기다리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = BusyDriver()
    monkeypatch.setattr(workflow, "execute_activity", driver.execute_activity)
    monkeypatch.setattr(asyncio, "sleep", driver.sleep)

    with pytest.raises(ApplicationError) as raised:
        await ChatExecutionWorkflow().run(ChatExecutionRequest("e1", "t1"))

    assert raised.value.type == THREAD_BUSY_FAILURE
    assert driver.prepared == THREAD_BUSY_MAX_ROUNDS
    assert driver.failed == ["e1"]
