"""대화 실행의 큐와 워크플로 이름과 액티비티 배치가 계약과 같은지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.config import DEFAULT_TASK_QUEUE_PREFIX
from tracer_agent.shared.workflows.chat_spec import (
    CHAT_EXECUTION_WORKFLOW,
    CHAT_QUEUE_KEY,
    CHAT_TASK_QUEUE,
    CHAT_THREAD_WORKFLOW,
    FAIL_ACTIVITY,
    FINALIZE_ACTIVITY,
    GENERATE_ACTIVITY,
    PREPARE_ACTIVITY,
    STOP_BUDGET_LANDED,
    STOP_CANCELED,
    STOP_COMPLETED,
    execution_workflow_id,
    thread_workflow_id,
)

_CONTRACT = workflow_contract("queues.yaml")
_WORKFLOWS = _CONTRACT["workflows"]


def test_큐_이름을_접두사와_계약의_키가_만든다() -> None:
    assert CHAT_QUEUE_KEY in _CONTRACT["queues"]
    assert CHAT_TASK_QUEUE.split("-", 1) == [DEFAULT_TASK_QUEUE_PREFIX, CHAT_QUEUE_KEY]


def test_워크플로_이름이_계약이_적은_이름과_같다() -> None:
    assert _WORKFLOWS["chatThread"]["name"] == CHAT_THREAD_WORKFLOW
    assert _WORKFLOWS["chatExecution"]["name"] == CHAT_EXECUTION_WORKFLOW


def test_워크플로_식별자가_계약이_적은_모양을_따른다() -> None:
    assert thread_workflow_id("t1") == _WORKFLOWS["chatThread"]["id"].format(threadId="t1")
    assert execution_workflow_id("e1") == _WORKFLOWS["chatExecution"]["id"].format(executionId="e1")


def test_스레드와_실행의_워크플로_식별자가_서로_겹치지_않는다() -> None:
    assert thread_workflow_id("t1") != execution_workflow_id("t1")


def test_두_워크플로가_같은_큐에서_실행된다() -> None:
    assert _WORKFLOWS["chatThread"]["queue"] == CHAT_QUEUE_KEY
    assert _WORKFLOWS["chatExecution"]["queue"] == CHAT_QUEUE_KEY


def test_액티비티_이름과_배치가_계약이_적은_것과_같다() -> None:
    activities = _WORKFLOWS["chatExecution"]["activities"]

    assert [activity["name"] for activity in activities] == [
        PREPARE_ACTIVITY,
        GENERATE_ACTIVITY,
        FINALIZE_ACTIVITY,
        FAIL_ACTIVITY,
    ]
    assert {activity["queue"] for activity in activities} == {CHAT_QUEUE_KEY}


def test_정지_사유가_실행_어휘와_같다() -> None:
    assert (STOP_COMPLETED, STOP_BUDGET_LANDED, STOP_CANCELED) == ("completed", "budget_landed", "canceled")
