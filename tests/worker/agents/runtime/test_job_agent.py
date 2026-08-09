"""잡 하나가 자기 산출물을 배달하는 조건과 템플릿이 요구하는 자리를 검증한다(네트워크 없음)."""

from __future__ import annotations

from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind
from tracer_agent.worker.agents.recipe_scan.agent import RECIPE_SCAN_JOB
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.job_agent import JobGraphAgent
from tracer_agent.worker.agents.task_cleanup.agent import TASK_CLEANUP_JOB
from tracer_agent.worker.agents.title_suggestion.agent import TITLE_SUGGESTION_JOB

_JOBS: tuple[JobGraphAgent[object, object], ...] = (
    TITLE_SUGGESTION_JOB,
    TASK_CLEANUP_JOB,
    RECIPE_SCAN_JOB,
)


async def test_배달할_창구가_없는_잡은_창구를_부르지_않는다() -> None:
    tracer = FakeTracerApi()

    await TITLE_SUGGESTION_JOB.settle_outputs(tracer, "job-6", {"suggestions": [{"title": "제목"}]}, {})

    assert tracer.posts == []


async def test_산출물이_없으면_배달을_부르지_않는다() -> None:
    for job in _JOBS:
        tracer = FakeTracerApi()

        await job.settle_outputs(tracer, "job-7", None, {})
        await job.settle_outputs(tracer, "job-8", {}, {})

        assert tracer.posts == [], job.kind


async def test_산출물이_있는_잡만_자기_창구를_부른다() -> None:
    tracer = FakeTracerApi()

    await RECIPE_SCAN_JOB.settle_outputs(tracer, "job-9", {"recipes": [{"title": "하나"}]}, {})

    assert [post["path"] for post in tracer.posts] == ["/api/v1/recipes"]


async def test_문맥을_모으지_않는_잡은_실행_입력을_그대로_낸다() -> None:
    # 스캔은 접수가 실은 값만으로 요청이 서므로 기본 구현이 payload를 그대로 내야 한다.
    payload = {"taskId": "task-1", "userId": "user-1"}

    assert await RECIPE_SCAN_JOB.collect_context(payload, FakeTracerApi()) == payload


def test_잡마다_자기_종류와_위상과_재귀_상한을_스스로_갖는다() -> None:
    kinds = {job.kind for job in _JOBS}

    assert kinds == {
        AgentJobKind.TITLE_SUGGESTION,
        AgentJobKind.TASK_CLEANUP,
        AgentJobKind.RECIPE_SCAN,
    }
    for job in _JOBS:
        assert job.recursion_limit > 0, job.kind
        assert job.topology is not None, job.kind
