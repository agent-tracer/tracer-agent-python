"""title-suggestion의 표준 LangChain agent를 도구 레지스트리로 컴파일한다."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.prompt_cache import PromptCacheMiddleware
from ..runtime.llm.standard_agent import StandardAgentMiddleware
from .tools import TitleToolContext


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
        ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="error"),
        StandardAgentMiddleware(),
        # 남은 몫을 알리는 꼬리가 붙은 뒤에 서야 경계를 그 꼬리 앞에 놓을 수 있다.
        PromptCacheMiddleware(ttl="1h"),
    ]
    if fallback_chat is not None:
        middleware.append(FallbackModelMiddleware(fallback_chat))
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=system,
        middleware=middleware,
        response_format=ToolStrategy(TitleSuggestionDraft, handle_errors=True),
        context_schema=TitleToolContext,
        name="title-suggestion-investigator",
    )
