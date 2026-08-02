"""title-suggestion의 표준 LangChain agent를 도구 레지스트리로 컴파일한다."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.standard_agent import StandardAgentContext, StandardAgentMiddleware


def build_title_agent(
    chat: BaseChatModel,
    system_prompt: str,
    tools: list[BaseTool],
    *,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """표준 도구 실행과 구조화 출력을 갖춘 title agent를 컴파일한다."""
    system = SystemMessage(content=system_prompt)
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        # 시스템 프롬프트와 도구 선언이 턴마다 같으므로 그 둘이 캐시 접두사가 된다.
        AnthropicPromptCachingMiddleware(ttl="1h"),
        ModelCallLimitMiddleware(run_limit=max_turns + 2, exit_behavior="error"),
        StandardAgentMiddleware(),
    ]
    if fallback_chat is not None:
        middleware.append(FallbackModelMiddleware(fallback_chat))
    # noinspection PyTypeChecker
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=system,
        middleware=middleware,
        response_format=ToolStrategy(TitleSuggestionDraft, handle_errors=True),
        context_schema=StandardAgentContext,
        name="title-suggestion-investigator",
    )
