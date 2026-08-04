"""워커에 등록될 잡 워크플로가 이름과 입력 타입과 단계 경계를 계약대로 내놓는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from temporalio import workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import _Definition

from tracer_agent.shared.workflows.jobs_spec import (
    AGENT_JOB_WORKFLOW,
    FINALIZE_AGENT_JOB_ACTIVITY,
    GENERATE_AGENT_JOB_ACTIVITY,
    GENERATE_TASK_QUEUE,
    JOB_GENERATE_MAX_ATTEMPTS,
    JOB_PREPARE_MAX_ATTEMPTS,
    PREPARE_AGENT_JOB_ACTIVITY,
    SETTLE_CANCELED_JOB_ACTIVITY,
    AgentJobRequest,
)
from tracer_agent.worker.workflows.jobs_workflows import AgentJobWorkflow


def _stage_recorder(scheduled: list[dict[str, Any]]) -> Any:
    async def record(name: str, _arg: Any = None, **options: Any) -> Any:
        scheduled.append({"name": name, **options})
        if name == GENERATE_AGENT_JOB_ACTIVITY:
            return {"outcome": {}, "response": {}}
        return {}

    return record


async def _run_stages() -> list[dict[str, Any]]:
    scheduled: list[dict[str, Any]] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(workflow, "execute_activity", _stage_recorder(scheduled))
        await AgentJobWorkflow().run(AgentJobRequest("title-suggestion", {}))
    return scheduled


def test_워크플로_이름이_등록_이름과_같다() -> None:
    found = workflow._Definition.from_class(AgentJobWorkflow)
    assert found is not None
    assert found.name == AGENT_JOB_WORKFLOW


def test_워크플로가_잡_요청_하나를_입력으로_받는다() -> None:
    found = workflow._Definition.from_class(AgentJobWorkflow)
    assert found is not None
    assert found.arg_types == [AgentJobRequest]


def test_취소_닫기_액티비티_이름이_생성_액티비티와_갈린다() -> None:
    assert SETTLE_CANCELED_JOB_ACTIVITY != GENERATE_AGENT_JOB_ACTIVITY


async def test_준비와_생성과_종결을_그_차례로_실행한다() -> None:
    scheduled = await _run_stages()

    assert [stage["name"] for stage in scheduled] == [
        PREPARE_AGENT_JOB_ACTIVITY,
        GENERATE_AGENT_JOB_ACTIVITY,
        FINALIZE_AGENT_JOB_ACTIVITY,
    ]


async def test_모델을_부르는_생성만_생성_큐에_얹는다() -> None:
    scheduled = await _run_stages()

    queued = {stage["name"]: stage.get("task_queue") for stage in scheduled}
    assert queued[GENERATE_AGENT_JOB_ACTIVITY] == GENERATE_TASK_QUEUE
    assert queued[PREPARE_AGENT_JOB_ACTIVITY] is None
    assert queued[FINALIZE_AGENT_JOB_ACTIVITY] is None


async def test_단계마다_자기_재시도_상한을_갖는다() -> None:
    scheduled = await _run_stages()

    attempts = {stage["name"]: stage["retry_policy"].maximum_attempts for stage in scheduled}
    assert attempts[PREPARE_AGENT_JOB_ACTIVITY] == JOB_PREPARE_MAX_ATTEMPTS
    assert attempts[GENERATE_AGENT_JOB_ACTIVITY] == JOB_GENERATE_MAX_ATTEMPTS
    # 유료 모델 호출을 되풀이하지 않도록 생성의 상한이 준비보다 낮다.
    assert JOB_GENERATE_MAX_ATTEMPTS < JOB_PREPARE_MAX_ATTEMPTS


async def test_워크플로가_샌드박스에서_다시_읽혀도_선다() -> None:
    # 샌드박스가 워크플로의 import 그래프를 다시 실행하므로 모듈을 읽을 때 파일 경로를 푸는 자리가 있으면 워커가 서지 못한다.
    SandboxedWorkflowRunner().prepare_workflow(_Definition.must_from_class(AgentJobWorkflow))
