"""잡 큐와 워크플로 이름과 긴 생성의 큐 배치가 계약과 같은지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.config import DEFAULT_TASK_QUEUE_PREFIX
from tracer_agent.shared.workflows.chat_spec import CHAT_TASK_QUEUE
from tracer_agent.shared.workflows.jobs_spec import (
    AGENT_JOB_WORKFLOW,
    GENERATE_AGENT_JOB_ACTIVITY,
    GENERATE_QUEUE_KEY,
    GENERATE_TASK_QUEUE,
    JOBS_QUEUE_KEY,
    JOBS_TASK_QUEUE,
    PREPARE_AGENT_JOB_ACTIVITY,
    SETTLE_CANCELED_JOB_ACTIVITY,
    agent_job_workflow_id,
)

_CONTRACT = workflow_contract("queues.yaml")
_AGENT_JOB = _CONTRACT["jobWorkflows"]["singleKind"]["agentJob"]
_QUEUE_OF = {activity["name"]: activity["queue"] for activity in _AGENT_JOB["activities"]}


def test_큐_이름을_접두사와_계약의_키가_만든다() -> None:
    assert {JOBS_QUEUE_KEY, GENERATE_QUEUE_KEY} <= set(_CONTRACT["queues"])
    assert JOBS_TASK_QUEUE.split("-", 1) == [DEFAULT_TASK_QUEUE_PREFIX, JOBS_QUEUE_KEY]
    assert GENERATE_TASK_QUEUE.split("-", 1) == [DEFAULT_TASK_QUEUE_PREFIX, GENERATE_QUEUE_KEY]


def test_세_큐가_서로_나뉘어_있다() -> None:
    assert len({JOBS_TASK_QUEUE, GENERATE_TASK_QUEUE, CHAT_TASK_QUEUE}) == 3


def test_워크플로_이름이_계약이_적은_이름과_같다() -> None:
    assert _AGENT_JOB["name"] == AGENT_JOB_WORKFLOW
    assert _AGENT_JOB["queue"] == JOBS_QUEUE_KEY


def test_워크플로_식별자가_계약이_적은_모양을_따른다() -> None:
    template = _AGENT_JOB["id"]

    assert agent_job_workflow_id("recipe-scan", "k1") == template.format(kind="recipe-scan", key="k1")


def test_워크플로_식별자가_잡_종류마다_나뉜다() -> None:
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("recipe-scan", "k1")
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("title-suggestion", "k2")


def test_모델을_부르는_생성_액티비티가_generate_큐에서_실행된다() -> None:
    assert _QUEUE_OF[GENERATE_AGENT_JOB_ACTIVITY] == GENERATE_QUEUE_KEY


def test_모델을_부르지_않는_준비_액티비티가_jobs_큐에서_실행된다() -> None:
    assert _QUEUE_OF[PREPARE_AGENT_JOB_ACTIVITY] == JOBS_QUEUE_KEY


def test_취소_닫기_액티비티가_jobs_큐에서_실행된다() -> None:
    assert _QUEUE_OF[SETTLE_CANCELED_JOB_ACTIVITY] == JOBS_QUEUE_KEY
