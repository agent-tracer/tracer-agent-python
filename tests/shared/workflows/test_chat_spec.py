"""두 구현체가 같은 실행을 가져가지 않도록 큐와 워크플로 식별자가 나뉘었는지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.workflows.chat_spec import (
    CHAT_EXECUTION_WORKFLOW,
    CHAT_TASK_QUEUE,
    CHAT_THREAD_WORKFLOW,
    STOP_BUDGET_LANDED,
    STOP_CANCELED,
    STOP_COMPLETED,
    execution_workflow_id,
    thread_workflow_id,
)

_IMPLEMENTATIONS = workflow_contract("queues.yaml")["implementations"]
_TYPESCRIPT = _IMPLEMENTATIONS["typescript"]
_PYTHON = _IMPLEMENTATIONS["python"]


def test_큐가_계약이_적은_이름과_같다() -> None:
    assert _PYTHON["queues"]["chat"]["name"] == CHAT_TASK_QUEUE


def test_큐가_TypeScript_구현체의_것과_겹치지_않는다() -> None:
    assert CHAT_TASK_QUEUE not in {queue["name"] for queue in _TYPESCRIPT["queues"].values()}


def test_워크플로_이름이_계약이_적은_이름과_같다() -> None:
    assert _PYTHON["workflows"]["chatThread"]["name"] == CHAT_THREAD_WORKFLOW
    assert _PYTHON["workflows"]["chatExecution"]["name"] == CHAT_EXECUTION_WORKFLOW


def test_워크플로_이름이_TypeScript_구현체의_것과_겹치지_않는다() -> None:
    names = {workflow["name"] for workflow in _TYPESCRIPT["workflows"].values()}

    assert CHAT_EXECUTION_WORKFLOW not in names
    assert CHAT_THREAD_WORKFLOW not in names


def test_워크플로_식별자가_TypeScript_구현체의_규약과_겹치지_않는다() -> None:
    typescript = _TYPESCRIPT["workflows"]

    assert thread_workflow_id("t1") != typescript["chatThread"]["id"].format(threadId="t1")
    assert execution_workflow_id("e1") != typescript["chatExecution"]["id"].format(executionId="e1")
    assert thread_workflow_id("t1") != execution_workflow_id("t1")


def test_워크플로_식별자가_계약이_적은_모양을_따른다() -> None:
    python = _PYTHON["workflows"]

    assert thread_workflow_id("t1") == python["chatThread"]["id"].format(threadId="t1")
    assert execution_workflow_id("e1") == python["chatExecution"]["id"].format(executionId="e1")


def test_정지_사유가_실행_어휘와_같다() -> None:
    assert (STOP_COMPLETED, STOP_BUDGET_LANDED, STOP_CANCELED) == ("completed", "budget_landed", "canceled")
