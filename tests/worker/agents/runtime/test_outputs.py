"""종결한 잡의 산출물이 추적 창구로 실려 나가는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import FakeTracerApi
from tracer_agent.worker.agents.runtime.outputs import (
    CLEANUP_SUGGESTIONS_PATH,
    MAX_RECIPES,
    RECIPES_PATH,
    deliver_job_outputs,
)


def _tracer() -> Any:
    return FakeTracerApi()


async def test_레시피_후보를_한_벌로_보내고_출처_잡을_함께_싣는다() -> None:
    tracer = _tracer()

    await deliver_job_outputs(
        tracer, "recipe-scan", "job-1", {"recipes": [{"title": "하나"}, {"title": "둘"}]}
    )

    assert [post["path"] for post in tracer.posts] == [RECIPES_PATH]
    body = tracer.posts[0]["body"]
    assert len(body["recipes"]) == 2
    assert body["author"] == "agent"
    # 같은 잡의 재시도가 후보를 두 벌 만들지 않도록 창구가 이 값으로 멱등을 판정한다.
    assert body["sourceJobId"] == "job-1"


async def test_정리_제안을_한_벌로_보내고_잡을_함께_싣는다() -> None:
    tracer = _tracer()

    await deliver_job_outputs(
        tracer, "task-cleanup", "job-2", {"suggestions": [{"taskId": "t1", "kind": "archive"}]}
    )

    assert [post["path"] for post in tracer.posts] == [CLEANUP_SUGGESTIONS_PATH]
    assert tracer.posts[0]["body"]["jobId"] == "job-2"


async def test_창구가_받는_상한까지만_보낸다() -> None:
    tracer = _tracer()

    await deliver_job_outputs(
        tracer, "recipe-scan", "job-3", {"recipes": [{"title": str(n)} for n in range(MAX_RECIPES + 5)]}
    )

    assert len(tracer.posts[0]["body"]["recipes"]) == MAX_RECIPES


async def test_산출물이_없으면_창구를_부르지_않는다() -> None:
    tracer = _tracer()

    await deliver_job_outputs(tracer, "recipe-scan", "job-4", {"recipes": []})
    await deliver_job_outputs(tracer, "task-cleanup", "job-5", None)
    # 제목 제안은 산출물이 없는 잡이다.
    await deliver_job_outputs(tracer, "title-suggestion", "job-6", {"title": "제목"})

    assert tracer.posts == []


async def test_창구가_실패해도_종결을_되돌리지_않는다() -> None:
    class Unavailable:
        async def post(self, _path: str, _body: dict[str, Any]) -> Any:
            raise ConnectionError("tracer api down")

    # 원장은 이미 닫혔으므로 산출물이 서지 않은 채로 잡은 완료로 남는다.
    await deliver_job_outputs(
        Unavailable(),  # type: ignore[arg-type]
        "recipe-scan",
        "job-7",
        {"recipes": [{"title": "하나"}]},
    )
