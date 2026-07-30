"""두 구현체가 같은 실행을 가져가지 않도록 잡 큐와 워크플로 식별자가 나뉘었는지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.workflows.chat_spec import CHAT_TASK_QUEUE
from tracer_agent.shared.workflows.jobs_spec import GRAPH_JOB_QUEUE, agent_job_workflow_id

_QUEUES = workflow_contract("queues.yaml")["queues"]


def test_계약이_잡_큐_자리를_갖는다() -> None:
    assert "jobs" in _QUEUES


def test_잡_큐가_chat_큐와_나뉘어_있다() -> None:
    assert GRAPH_JOB_QUEUE != CHAT_TASK_QUEUE


def test_워크플로_식별자가_잡_종류마다_나뉜다() -> None:
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("recipe-scan", "k1")
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("title-suggestion", "k2")
