"""잡 하나가 자기 산출물을 적는 조건과 템플릿이 요구하는 자리를 검증한다(네트워크 없음)."""

from __future__ import annotations

from tests.support.chat_surface import SingleSql
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind
from tracer_agent.worker.agents.recipe_scan.agent import RECIPE_SCAN_JOB
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.job_agent import JobGraphAgent
from tracer_agent.worker.agents.runtime.outputs import JobOutputTargets
from tracer_agent.worker.agents.task_cleanup.agent import TASK_CLEANUP_JOB
from tracer_agent.worker.agents.title_suggestion.agent import TITLE_SUGGESTION_JOB

_JOBS: tuple[JobGraphAgent[object, object], ...] = (
    TITLE_SUGGESTION_JOB,
    TASK_CLEANUP_JOB,
    RECIPE_SCAN_JOB,
)


def _targets(store: SqliteLedgerSql) -> JobOutputTargets:
    return JobOutputTargets(SingleSql(store), FakeTracerApi())


async def test_적을_원장이_없는_잡은_원장을_열지_않는다() -> None:
    store = SqliteLedgerSql()

    await TITLE_SUGGESTION_JOB.settle_outputs(
        _targets(store), "job-6", {"suggestions": [{"title": "제목"}]}, {"userId": "user-1"}
    )

    assert store.rows("recipes") == []
    assert store.rows("task_cleanup_suggestions") == []
    store.close()


async def test_산출물이_없으면_원장을_열지_않는다() -> None:
    for job in _JOBS:
        store = SqliteLedgerSql()

        await job.settle_outputs(_targets(store), "job-7", None, {"userId": "user-1"})
        await job.settle_outputs(_targets(store), "job-8", {}, {"userId": "user-1"})

        assert store.rows("recipes") == [], job.kind
        store.close()


async def test_사용자를_모르는_산출물은_적지_않는다() -> None:
    store = SqliteLedgerSql()

    await RECIPE_SCAN_JOB.settle_outputs(_targets(store), "job-9", {"recipes": [{"title": "하나"}]}, {})

    assert store.rows("recipes") == []
    store.close()


async def test_산출물이_있는_잡만_자기_원장에_적는다() -> None:
    store = SqliteLedgerSql()

    await RECIPE_SCAN_JOB.settle_outputs(
        _targets(store), "job-9", {"recipes": [{"title": "하나"}]}, {"userId": "user-1"}
    )

    assert [row["title"] for row in store.rows("recipes")] == ["하나"]
    store.close()


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
