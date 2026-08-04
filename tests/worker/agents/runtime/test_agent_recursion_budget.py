"""세 agent의 재귀 한도가 선언한 모델 턴 상한을 실제로 처리하는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph.state import CompiledStateGraph

from tests.support.agents import mk_cleanup_agent, mk_recipe_agent, mk_title_agent
from tests.support.fakes import FakeToolLoopChat
from tracer_agent.worker.agents.recipe_scan.tools import RECIPE_TOOLS
from tracer_agent.worker.agents.runtime.llm.structured_agent import recursion_limit_for
from tracer_agent.worker.agents.task_cleanup.tools import CLEANUP_TOOLS
from tracer_agent.worker.agents.title_suggestion.tools import TITLE_TOOLS

# 봉투가 실어 오는 값이라 테스트는 카탈로그의 최대치를 대표로 잡아 유도 규칙만 검증한다.
_TURNS = 16


def _loop_supersteps(agent: CompiledStateGraph[Any, Any, Any, Any]) -> int:
    return len([name for name in agent.get_graph().nodes if not name.startswith("__")])


def _agents() -> list[tuple[str, CompiledStateGraph[Any, Any, Any, Any], int, int]]:
    chat = FakeToolLoopChat([])
    return [
        (
            "task-cleanup",
            mk_cleanup_agent(
                chat,
                CLEANUP_TOOLS.langchain_tools(),
                CLEANUP_TOOLS.transient_errors(),
                max_turns=_TURNS,
            ),
            _TURNS,
            recursion_limit_for(_TURNS),
        ),
        (
            "recipe-scan",
            mk_recipe_agent(
                chat,
                RECIPE_TOOLS.langchain_tools(),
                RECIPE_TOOLS.transient_errors(),
                max_turns=_TURNS,
            ),
            _TURNS,
            recursion_limit_for(_TURNS),
        ),
        (
            "title-suggestion",
            mk_title_agent(
                chat,
                TITLE_TOOLS.langchain_tools(),
                TITLE_TOOLS.transient_errors(),
                max_turns=_TURNS,
            ),
            _TURNS,
            recursion_limit_for(_TURNS),
        ),
    ]


@pytest.mark.parametrize(("name", "agent", "turns", "limit"), _agents())
def test_재귀_한도가_선언한_모델_턴을_감당한다(
    name: str,
    agent: CompiledStateGraph[Any, Any, Any, Any],
    turns: int,
    limit: int,
) -> None:
    supported = limit // _loop_supersteps(agent)

    assert supported >= turns, (
        f"{name}: 재귀 한도 {limit}는 모델 턴 {supported}회까지만 허용해 선언한 예산 {turns}회에 못 미친다"
    )
