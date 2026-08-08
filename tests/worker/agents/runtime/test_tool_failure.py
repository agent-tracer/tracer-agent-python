"""도구 하나의 실패가 잡 실패로 번지지 않는지 세 잡 에이전트에서 검증한다(페이크 모델)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from tests.support.agents import mk_cleanup_agent, mk_recipe_agent, mk_title_agent
from tests.support.fakes import FakeToolLoopChat
from tests.support.tool_contexts import mk_cleanup_context, mk_recipe_context, mk_title_context
from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft
from tracer_agent.shared.agents.task_cleanup.models import CleanupDraft
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft
from tracer_agent.worker.agents.runtime.errors import BudgetExceeded, DeadlineExceeded, OutputTruncated

TOOL_NAME = "get_task_events"

_DRAFTS: dict[str, Any] = {
    "recipe-scan": {"recipes": []},
    "task-cleanup": {"suggestions": []},
    "title-suggestion": {"suggestions": []},
}

_AGENTS: dict[str, tuple[Callable[..., Any], Callable[[], Any], type[Any]]] = {
    "recipe-scan": (mk_recipe_agent, mk_recipe_context, RecipeDraft),
    "task-cleanup": (mk_cleanup_agent, mk_cleanup_context, CleanupDraft),
    "title-suggestion": (mk_title_agent, mk_title_context, TitleSuggestionDraft),
}


def _failing_tool(error: BaseException) -> Any:
    @tool(TOOL_NAME)
    def get_task_events(taskId: str) -> str:  # noqa: ARG001
        """맡은 태스크 이벤트를 읽되 늘 실패한다."""
        raise error

    return get_task_events


def _chat(agent_name: str) -> FakeToolLoopChat:
    return FakeToolLoopChat(
        [
            [{"name": TOOL_NAME, "args": {"taskId": "t1"}}],
            _DRAFTS[agent_name],
        ]
    )


async def _run(agent_name: str, error: BaseException) -> Any:
    build, context, _output = _AGENTS[agent_name]
    agent = build(_chat(agent_name), [_failing_tool(error)], (), max_turns=5)
    return await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]},
        context=context(),
        config={"recursion_limit": 100},
    )


def _failed(output: Any) -> list[ToolMessage]:
    return [
        message
        for message in output["messages"]
        if isinstance(message, ToolMessage) and message.name == TOOL_NAME
    ]


@pytest.mark.parametrize("agent_name", sorted(_AGENTS))
async def test_도구_실패는_잡_실패로_번지지_않는다(agent_name: str) -> None:
    output = await _run(agent_name, ValueError("the read API answered 403"))

    assert isinstance(output.get("structured_response"), _AGENTS[agent_name][2])
    failed = _failed(output)
    assert [message.status for message in failed] == ["error"]
    assert "the read API answered 403" in str(failed[0].content)


@pytest.mark.parametrize("agent_name", sorted(_AGENTS))
async def test_실패_문구는_계약이_소유한_한_벌이다(agent_name: str) -> None:
    output = await _run(agent_name, ValueError("boom"))

    content = str(_failed(output)[0].content)
    assert content.startswith(f"Tool {TOOL_NAME} failed: boom.")
    assert "Do not call it again more than once." in content


@pytest.mark.parametrize(
    "halting",
    [
        GraphBubbleUp(),
        BudgetExceeded("execution model budget"),
        OutputTruncated("structured output truncated"),
        DeadlineExceeded("agent deadline exceeded"),
    ],
    ids=["bubble-up", "budget", "truncated", "deadline"],
)
async def test_실행_전체를_멈추는_신호는_그대로_올라온다(halting: BaseException) -> None:
    with pytest.raises(type(halting)):
        await _run("recipe-scan", halting)
