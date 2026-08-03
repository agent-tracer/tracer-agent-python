"""잡 하나가 자기 산출물을 배달하는 조건을 검증한다(네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import FakeTracerApi
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind
from tracer_agent.worker.agents.runtime.job_agent import JobAgent
from tracer_agent.worker.agents.title_suggestion.agent import TITLE_SUGGESTION_JOB


async def test_배달할_창구가_없는_잡은_창구를_부르지_않는다() -> None:
    tracer = FakeTracerApi()

    await TITLE_SUGGESTION_JOB.settle_outputs(tracer, "job-6", {"suggestions": [{"title": "제목"}]})  # type: ignore[arg-type]

    assert tracer.posts == []


async def test_산출물이_없으면_배달을_부르지_않는다() -> None:
    delivered: list[str] = []

    async def deliver(_tracer: Any, execution_id: str, _data: dict[str, Any]) -> None:
        delivered.append(execution_id)

    job: JobAgent[Any] = JobAgent(
        kind=AgentJobKind.RECIPE_SCAN,
        prepare=TITLE_SUGGESTION_JOB.prepare,
        run=TITLE_SUGGESTION_JOB.run,
        deliver=deliver,
    )

    await job.settle_outputs(FakeTracerApi(), "job-7", None)  # type: ignore[arg-type]
    await job.settle_outputs(FakeTracerApi(), "job-8", {})  # type: ignore[arg-type]

    assert delivered == []
