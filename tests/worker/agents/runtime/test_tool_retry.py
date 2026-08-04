"""도구 계층의 일시 오류 재시도를 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain.tools import tool

from tests.support.agents import mk_recipe_agent
from tests.support.fakes import FakeToolLoopChat
from tests.support.tool_contexts import mk_recipe_context
from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft
from tracer_agent.worker.agents.recipe_scan.tools import RECIPE_TOOLS, RecipeToolContext
from tracer_agent.worker.agents.runtime.tracer_client import TracerApiUnavailable
from tracer_agent.worker.agents.task_cleanup.tools import GetTaskEventsTool

RECIPE_TRANSIENT = RECIPE_TOOLS.transient_errors()

CLEANUP_TRANSIENT = GetTaskEventsTool.transient_errors


def _flaky_tool(fail_times: int, error: BaseException) -> tuple[Any, list[int]]:
    calls = [0]

    @tool("get_task_events")
    def get_task_events(taskId: str) -> str:  # noqa: ARG001
        """맡은 태스크 이벤트를 읽되 앞선 몇 번은 오류를 낸다."""
        calls[0] += 1
        if calls[0] <= fail_times:
            raise error
        return '{"events": [], "truncated": false, "total": 0}'

    return get_task_events, calls


def _context() -> RecipeToolContext:
    return mk_recipe_context()


def test_재시도_대상은_연결_계열_일시_오류만이다() -> None:
    # 목록을 통째로 고정해 원장과 색인의 오류가 다시 끼어들면 검사가 깨지게 한다.
    assert set(RECIPE_TRANSIENT) == {TracerApiUnavailable, ConnectionError, TimeoutError}
    # 두 에이전트가 같은 창구를 부르므로 재시도 대상이 같다.
    assert set(CLEANUP_TRANSIENT) == set(RECIPE_TRANSIENT)
    # 검증·도메인 오류는 이 목록에 없다.
    assert ValueError not in RECIPE_TRANSIENT


async def test_일시_오류는_도구_계층에서_재시도해_실행이_이어진다() -> None:
    flaky, calls = _flaky_tool(1, ConnectionError("transient blip"))
    chat = FakeToolLoopChat(
        [
            [{"name": "get_task_events", "args": {"taskId": "t1"}}],
            {"recipes": []},
        ]
    )
    agent = mk_recipe_agent(chat, [flaky], RECIPE_TRANSIENT, max_turns=5, output=RecipeDraft)

    output = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]},
        context=_context(),
        config={"recursion_limit": 100},
    )

    # 첫 호출이 끊겨도 도구를 한 번 더 부르고, 조사는 실패하지 않고 이어진다.
    assert calls[0] == 2
    assert isinstance(output.get("structured_response"), RecipeDraft)


async def test_소진해도_실패하면_오류가_그대로_올라온다() -> None:
    flaky, calls = _flaky_tool(9, TracerApiUnavailable("tracer api down"))
    chat = FakeToolLoopChat([[{"name": "get_task_events", "args": {"taskId": "t1"}}]])
    agent = mk_recipe_agent(chat, [flaky], RECIPE_TRANSIENT, max_turns=5, output=RecipeDraft)

    with pytest.raises(TracerApiUnavailable):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            context=_context(),
            config={"recursion_limit": 100},
        )

    # 최초 1회 + 재시도 2회로 세 번 시도한 뒤 실패 의미를 보존해 올린다.
    assert calls[0] == 3


async def test_도메인_오류는_재시도하지_않는다() -> None:
    flaky, calls = _flaky_tool(9, ValueError("bad citation"))
    chat = FakeToolLoopChat([[{"name": "get_task_events", "args": {"taskId": "t1"}}]])
    agent = mk_recipe_agent(chat, [flaky], RECIPE_TRANSIENT, max_turns=5, output=RecipeDraft)

    with pytest.raises(ValueError, match="bad citation"):
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]},
            context=_context(),
            config={"recursion_limit": 100},
        )

    # 검증·도메인 오류는 한 번 시도하고 곧장 올린다.
    assert calls[0] == 1
