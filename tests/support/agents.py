"""세 잡 에이전트를 프로덕션과 같은 공통 빌더로 컴파일하는 시험용 조립기다."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft
from tracer_agent.shared.agents.task_cleanup.models import CleanupDraft
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft
from tracer_agent.worker.agents.recipe_scan.tools import RecipeToolContext
from tracer_agent.worker.agents.runtime.llm.structured_agent import build_structured_agent
from tracer_agent.worker.agents.task_cleanup.tools import CleanupToolContext
from tracer_agent.worker.agents.title_suggestion.tools import TitleToolContext


def mk_recipe_agent(
    chat: BaseChatModel,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...] = (),
    *,
    max_turns: int,
    output: type[BaseModel] = RecipeDraft,
    fallback_chat: BaseChatModel | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return build_structured_agent(
        chat,
        "system",
        tools,
        transient_errors,
        output=output,
        context_schema=RecipeToolContext,
        name="recipe-scan-investigator",
        max_turns=max_turns,
        fallback_chat=fallback_chat,
    )


def mk_cleanup_agent(
    chat: BaseChatModel,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...] = (),
    *,
    max_turns: int,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return build_structured_agent(
        chat,
        "system",
        tools,
        transient_errors,
        output=CleanupDraft,
        context_schema=CleanupToolContext,
        name="task-cleanup-investigator",
        max_turns=max_turns,
        serializes_tools=True,
    )


def mk_title_agent(
    chat: BaseChatModel,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...] = (),
    *,
    max_turns: int,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return build_structured_agent(
        chat,
        "system",
        tools,
        transient_errors,
        output=TitleSuggestionDraft,
        context_schema=TitleToolContext,
        name="title-suggestion-investigator",
        max_turns=max_turns,
    )
