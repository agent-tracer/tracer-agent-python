"""워커에 등록될 잡 워크플로가 이름과 입력 타입을 계약대로 내놓는지 검증한다."""

from __future__ import annotations

from temporalio import workflow

from tracer_agent.shared.workflows.jobs_spec import (
    AGENT_JOB_WORKFLOW,
    RUN_AGENT_JOB_ACTIVITY,
    SETTLE_CANCELED_JOB_ACTIVITY,
    AgentJobRequest,
)
from tracer_agent.worker.workflows.jobs_workflows import AgentJobWorkflow


def test_워크플로_이름이_등록_이름과_같다() -> None:
    found = workflow._Definition.from_class(AgentJobWorkflow)
    assert found is not None
    assert found.name == AGENT_JOB_WORKFLOW


def test_워크플로가_잡_요청_하나를_입력으로_받는다() -> None:
    found = workflow._Definition.from_class(AgentJobWorkflow)
    assert found is not None
    assert found.arg_types == [AgentJobRequest]


def test_취소_닫기_액티비티_이름이_실행_액티비티와_갈린다() -> None:
    assert SETTLE_CANCELED_JOB_ACTIVITY != RUN_AGENT_JOB_ACTIVITY
