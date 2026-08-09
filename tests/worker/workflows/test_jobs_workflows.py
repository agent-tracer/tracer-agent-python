"""워커에 등록될 잡 워크플로가 이름과 입력 타입과 단계 경계를 계약대로 내놓는지 검증한다."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from temporalio import workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import _Definition

from tracer_agent.shared.agents.shared.job_kinds import AgentJobKind
from tracer_agent.shared.agents.shared.models import AgentResponse
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
    GeneratedAgentJob,
    JobOutcome,
)
from tracer_agent.worker.workflows.jobs_workflows import AgentJobWorkflow

SIGNAL_WAIT_S = 5.0


async def _reached(signal: asyncio.Event) -> None:
    """워크플로가 그 단계에 닿기를 기다리며, 닿지 못하면 정지가 아니라 실패로 낸다."""
    await asyncio.wait_for(signal.wait(), SIGNAL_WAIT_S)


def _stage_recorder(scheduled: list[dict[str, Any]]) -> Any:
    async def record(name: str, _arg: Any = None, **options: Any) -> Any:
        scheduled.append({"name": name, **options})
        if name == GENERATE_AGENT_JOB_ACTIVITY:
            return GeneratedAgentJob(
                outcome=JobOutcome(job_id="", user_id="local", status="completed", attempt=1),
                response=AgentResponse(modelUsed="model", durationMs=1),
            )
        return {}

    return record


async def _run_stages() -> list[dict[str, Any]]:
    scheduled: list[dict[str, Any]] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(workflow, "execute_activity", _stage_recorder(scheduled))
        await AgentJobWorkflow().run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, {}))
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


class TestCanceledJobSettles:
    """취소가 걸린 뒤에도 원장을 닫는 액티비티가 끝까지 실행되는지 고정한다."""

    @staticmethod
    def _canceling_stages(settled: list[str], generating: asyncio.Event, settling: asyncio.Event) -> Any:
        async def record(name: str, *_args: Any, **_options: Any) -> Any:
            if name == GENERATE_AGENT_JOB_ACTIVITY:
                generating.set()
                await asyncio.Event().wait()  # 취소로만 끝나도록 영원히 대기한다.
            if name == SETTLE_CANCELED_JOB_ACTIVITY:
                settling.set()
                # 종결이 진행 중일 때 취소가 다시 닿는 자리를 만든다.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                settled.append(name)
            return {}

        return record

    async def test_취소가_거듭_닿아도_원장을_취소로_닫는다(self) -> None:
        settled: list[str] = []
        generating = asyncio.Event()
        settling = asyncio.Event()
        request = AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, {"executionId": "job-1"})

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(workflow, "execute_activity", self._canceling_stages(settled, generating, settling))
            running = asyncio.ensure_future(AgentJobWorkflow().run(request))
            await _reached(generating)
            running.cancel()
            await _reached(settling)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

        assert settled == [SETTLE_CANCELED_JOB_ACTIVITY]

    async def test_실행_식별자가_없으면_닫을_행이_없어_아무_액티비티도_열지_않는다(self) -> None:
        settled: list[str] = []
        generating = asyncio.Event()
        settling = asyncio.Event()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(workflow, "execute_activity", self._canceling_stages(settled, generating, settling))
            running = asyncio.ensure_future(
                AgentJobWorkflow().run(AgentJobRequest(AgentJobKind.RECIPE_SCAN, {}))
            )
            await _reached(generating)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

        assert settled == []


class TestCanceledDuringFinalize:
    """생성이 끝난 뒤 종결 구간에 취소가 닿아도 종결 액티비티가 끝까지 실행되는지 고정한다."""

    @staticmethod
    def _finalizing_stages(done: list[str], finalizing: asyncio.Event) -> Any:
        async def record(name: str, *_args: Any, **_options: Any) -> Any:
            if name == FINALIZE_AGENT_JOB_ACTIVITY:
                finalizing.set()
                # 종결이 진행 중일 때 취소가 닿는 자리를 만든다.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            done.append(name)
            if name == GENERATE_AGENT_JOB_ACTIVITY:
                return GeneratedAgentJob(
                    outcome=JobOutcome(job_id="job-1", user_id="local", status="completed", attempt=1),
                    response=AgentResponse(modelUsed="model", durationMs=1),
                )
            return {}

        return record

    async def test_종결_중_취소가_닿아도_종결이_끝까지_실행된다(self) -> None:
        done: list[str] = []
        finalizing = asyncio.Event()
        request = AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, {"executionId": "job-1"})

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(workflow, "execute_activity", self._finalizing_stages(done, finalizing))
            running = asyncio.ensure_future(AgentJobWorkflow().run(request))
            await _reached(finalizing)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

        assert done == [
            PREPARE_AGENT_JOB_ACTIVITY,
            GENERATE_AGENT_JOB_ACTIVITY,
            FINALIZE_AGENT_JOB_ACTIVITY,
            SETTLE_CANCELED_JOB_ACTIVITY,
        ]


async def test_워크플로가_샌드박스에서_다시_읽혀도_선다() -> None:
    # 샌드박스가 워크플로의 import 그래프를 다시 실행하므로 모듈을 읽을 때 파일 경로를 푸는 자리가 있으면 워커가 서지 못한다.
    SandboxedWorkflowRunner().prepare_workflow(_Definition.must_from_class(AgentJobWorkflow))
