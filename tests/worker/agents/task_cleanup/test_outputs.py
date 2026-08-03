"""종결한 정리 스캔의 제안이 창구로 실려 나가는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from tests.support.fakes import FakeTracerApi
from tracer_agent.worker.agents.task_cleanup.outputs import (
    CLEANUP_SUGGESTIONS_PATH,
    MAX_SUGGESTIONS,
    deliver_suggestions,
)


async def test_정리_제안을_한_벌로_보내고_잡을_함께_싣는다() -> None:
    tracer = FakeTracerApi()

    await deliver_suggestions(tracer, "job-2", {"suggestions": [{"taskId": "t1", "kind": "archive"}]})  # type: ignore[arg-type]

    assert [post["path"] for post in tracer.posts] == [CLEANUP_SUGGESTIONS_PATH]
    assert tracer.posts[0]["body"]["jobId"] == "job-2"


async def test_창구가_받는_상한까지만_보낸다() -> None:
    tracer = FakeTracerApi()

    await deliver_suggestions(  # type: ignore[arg-type]
        tracer,
        "job-3",
        {"suggestions": [{"taskId": f"t{n}"} for n in range(MAX_SUGGESTIONS + 5)]},
    )

    assert len(tracer.posts[0]["body"]["suggestions"]) == MAX_SUGGESTIONS


async def test_제안이_없으면_창구를_부르지_않는다() -> None:
    tracer = FakeTracerApi()

    await deliver_suggestions(tracer, "job-5", {"suggestions": []})  # type: ignore[arg-type]

    assert tracer.posts == []
