"""워커에 등록될 워크플로 둘이 이름과 신호와 입력 타입을 계약대로 내놓는지 검증한다."""

from __future__ import annotations

from temporalio import workflow

from tracer_agent.shared.workflows.chat_spec import (
    CHAT_ENQUEUE_SIGNAL,
    CHAT_EXECUTION_WORKFLOW,
    CHAT_THREAD_WORKFLOW,
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
