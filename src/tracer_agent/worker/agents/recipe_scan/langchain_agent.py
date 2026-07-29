"""recipe-scan의 표준 LangChain agent를 도구 레지스트리로 컴파일한다."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.standard_agent import StandardAgentContext, StandardAgentMiddleware


def _tool_retry(transient_errors: tuple[type[Exception], ...]) -> ToolRetryMiddleware:
    return ToolRetryMiddleware(
        max_retries=2,
        retry_on=transient_errors,
        on_failure="error",
        backoff_factor=2.0,
        initial_delay=0.5,
        jitter=False,
    )


def build_recipe_agent(
    chat: BaseChatModel,
    system_prompt: str,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    output: type[BaseModel] = RecipeDraft,
    fallback_chat: BaseChatModel | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """표준 도구 실행과 구조화 출력을 갖춘 recipe-scan agent를 컴파일한다."""
    system = SystemMessage(
        content=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
    )
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        ModelCallLimitMiddleware(run_limit=max_turns + 2, exit_behavior="error"),
        StandardAgentMiddleware(),
        _tool_retry(transient_errors),
    ]
    if fallback_chat is not None:
        middleware.append(FallbackModelMiddleware(fallback_chat))
    # noinspection PyTypeChecker
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=system,
        middleware=middleware,
        response_format=ToolStrategy(output, handle_errors=True),
        context_schema=StandardAgentContext,
        name="recipe-scan-investigator",
    )
