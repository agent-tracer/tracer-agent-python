"""세 agent의 재귀 한도가 선언한 모델 턴 상한을 실제로 감당하는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph.state import CompiledStateGraph

from tests.support.fakes import FakeToolLoopChat
from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog
from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch
from tracer_agent.worker.agents.recipe_scan.langchain_agent import build_recipe_agent
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.recipe_scan.tools import build_recipe_registry as build_recipe_tool_registry
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.llm.structured_agent import recursion_limit_for
from tracer_agent.worker.agents.task_cleanup.langchain_agent import build_cleanup_agent
from tracer_agent.worker.agents.task_cleanup.reader import CleanupLedgerReader
from tracer_agent.worker.agents.task_cleanup.tools import build_cleanup_registry
from tracer_agent.worker.agents.title_suggestion.langchain_agent import build_title_agent
from tracer_agent.worker.agents.title_suggestion.reader import TitleLedgerReader
from tracer_agent.worker.agents.title_suggestion.tools import build_title_registry

# 봉투가 실어 오는 값이라 테스트는 카탈로그의 최대치를 대표로 잡아 유도 규칙만 검증한다.
_TURNS = 16


def _loop_supersteps(agent: CompiledStateGraph[Any, Any, Any, Any]) -> int:
    return len([name for name in agent.get_graph().nodes if not name.startswith("__")])


def _agents() -> list[tuple[str, CompiledStateGraph[Any, Any, Any, Any], int, int]]:
    chat = FakeToolLoopChat([])
    cleanup_registry = build_cleanup_registry(
        CleanupLedgerReader(FakeTracerApi()),
        CleanupBatch(),
        {},
        {},
        agent_name="task-cleanup",
    )
    recipe_registry = build_recipe_tool_registry(
        RecipeLedgerReader(FakeTracerApi()),
        RecipeSearchReader(FakeTracerApi()),
        ProvenanceCatalog(),
        agent_name="recipe-scan",
    )
    title_registry = build_title_registry(
        TitleLedgerReader(FakeTracerApi()),
        agent_name="title-suggestion",
    )
    return [
        (
            "task-cleanup",
            build_cleanup_agent(
                chat,
                "system",
                cleanup_registry.langchain_tools(),
                cleanup_registry.transient_errors(),
                max_turns=_TURNS,
            ),
            _TURNS,
            recursion_limit_for(_TURNS),
        ),
        (
            "recipe-scan",
            build_recipe_agent(
                chat,
                "system",
                recipe_registry.langchain_tools(),
                recipe_registry.transient_errors(),
                max_turns=_TURNS,
            ),
            _TURNS,
            recursion_limit_for(_TURNS),
        ),
        (
            "title-suggestion",
            build_title_agent(chat, "system", title_registry.langchain_tools(), max_turns=_TURNS),
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
