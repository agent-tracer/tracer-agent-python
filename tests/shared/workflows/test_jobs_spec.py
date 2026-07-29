"""두 구현체가 같은 실행을 가져가지 않도록 잡 큐와 워크플로 식별자가 나뉘었는지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.workflows.chat_spec import CHAT_TASK_QUEUE
from tracer_agent.shared.workflows.jobs_spec import (
    AGENT_JOB_WORKFLOW,
    GRAPH_JOB_QUEUE,
    agent_job_workflow_id,
)

_IMPLEMENTATIONS = workflow_contract("queues.yaml")["implementations"]
_TYPESCRIPT = _IMPLEMENTATIONS["typescript"]
_PYTHON = _IMPLEMENTATIONS["python"]


def test_잡_큐가_계약이_적은_이름과_같다() -> None:
    assert _PYTHON["queues"]["jobs"]["name"] == GRAPH_JOB_QUEUE


def test_잡_큐가_TypeScript_구현체의_잡_큐와_겹치지_않는다() -> None:
    assert GRAPH_JOB_QUEUE not in {queue["name"] for queue in _TYPESCRIPT["queues"].values()}


def test_잡_큐가_chat_큐와_나뉘어_있다() -> None:
    assert GRAPH_JOB_QUEUE != CHAT_TASK_QUEUE


def test_워크플로_식별자가_잡_종류마다_나뉜다() -> None:
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("recipe-scan", "k1")
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("title-suggestion", "k2")


def test_워크플로_이름이_계약이_적은_이름과_같다() -> None:
    assert _PYTHON["workflows"]["agentJob"]["name"] == AGENT_JOB_WORKFLOW


def test_워크플로_식별자가_계약이_적은_모양을_따른다() -> None:
    template = _PYTHON["workflows"]["agentJob"]["id"]

    assert agent_job_workflow_id("recipe-scan", "k1") == template.format(kind="recipe-scan", key="k1")
