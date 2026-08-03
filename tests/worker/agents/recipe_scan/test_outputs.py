"""종결한 스캔의 후보가 레시피 창구로 실려 나가는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import FakeTracerApi
from tracer_agent.worker.agents.recipe_scan.outputs import (
    MAX_RECIPES,
    RECIPES_PATH,
    deliver_recipes,
)


async def test_레시피_후보를_한_벌로_보내고_출처_잡을_함께_싣는다() -> None:
    tracer = FakeTracerApi()

    await deliver_recipes(tracer, "job-1", {"recipes": [{"title": "하나"}, {"title": "둘"}]})  # type: ignore[arg-type]

    assert [post["path"] for post in tracer.posts] == [RECIPES_PATH]
    body = tracer.posts[0]["body"]
    assert len(body["recipes"]) == 2
    assert body["author"] == "agent"
    # 같은 잡의 재시도가 후보를 두 벌 만들지 않도록 창구가 이 값으로 멱등을 판정한다.
    assert body["sourceJobId"] == "job-1"


async def test_창구가_받는_상한까지만_보낸다() -> None:
    tracer = FakeTracerApi()

    await deliver_recipes(  # type: ignore[arg-type]
        tracer, "job-3", {"recipes": [{"title": str(n)} for n in range(MAX_RECIPES + 5)]}
    )

    assert len(tracer.posts[0]["body"]["recipes"]) == MAX_RECIPES


async def test_후보가_없으면_창구를_부르지_않는다() -> None:
    tracer = FakeTracerApi()

    await deliver_recipes(tracer, "job-4", {"recipes": []})  # type: ignore[arg-type]

    assert tracer.posts == []


async def test_창구가_실패해도_종결을_되돌리지_않는다() -> None:
    class Unavailable:
        async def post(self, _path: str, _body: dict[str, Any]) -> Any:
            raise ConnectionError("tracer api down")

    # 원장은 이미 닫혔으므로 산출물이 서지 않은 채로 잡은 완료로 남는다.
    await deliver_recipes(Unavailable(), "job-7", {"recipes": [{"title": "하나"}]})  # type: ignore[arg-type]
