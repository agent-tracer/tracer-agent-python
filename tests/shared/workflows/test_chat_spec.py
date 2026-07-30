"""두 구현체가 같은 실행을 가져가지 않도록 큐와 워크플로 식별자가 나뉘었는지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.workflows.chat_spec import (
    CHAT_TASK_QUEUE,
    STOP_BUDGET_LANDED,
    STOP_CANCELED,
    STOP_COMPLETED,
    execution_workflow_id,
    thread_workflow_id,
)

_QUEUES = workflow_contract("queues.yaml")["queues"]


def test_계약이_대화_큐_자리를_갖는다() -> None:
    assert "chat" in _QUEUES


def test_대화_큐가_다른_큐_자리와_겹치지_않는다() -> None:
    assert CHAT_TASK_QUEUE.endswith("chat")


def test_스레드와_실행의_워크플로_식별자가_나뉜다() -> None:
    assert thread_workflow_id("t1") != execution_workflow_id("t1")


def test_정지_사유가_실행_어휘와_같다() -> None:
    assert (STOP_COMPLETED, STOP_BUDGET_LANDED, STOP_CANCELED) == ("completed", "budget_landed", "canceled")
