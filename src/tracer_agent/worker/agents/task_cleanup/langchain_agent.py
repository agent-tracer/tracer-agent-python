"""task-cleanup의 표준 LangChain agent를 도구 직렬화와 함께 조립한다."""

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
from pydantic import BaseModel

from tracer_agent.shared.agents.task_cleanup.models import CleanupDraft

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.retry import tool_retry_middleware
from ..runtime.llm.standard_agent import StandardAgentContext, StandardAgentMiddleware


def build_cleanup_agent(
    chat: BaseChatModel,
    system_prompt: str,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    output: type[BaseModel] = CleanupDraft,
    fallback_chat: BaseChatModel | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """표준 도구 실행과 구조화 출력을 갖춘 task-cleanup agent를 컴파일한다."""
    system = SystemMessage(content=system_prompt)
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        # 시스템 프롬프트와 도구 선언이 턴마다 같으므로 그 둘이 캐시 접두사가 된다.
        AnthropicPromptCachingMiddleware(ttl="1h"),
        ModelCallLimitMiddleware(run_limit=max_turns + 2, exit_behavior="error"),
        StandardAgentMiddleware(serialize_tools=True),
        tool_retry_middleware(transient_errors),
    ]
    if fallback_chat is not None:
        middleware.append(FallbackModelMiddleware(fallback_chat))
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=system,
        middleware=middleware,
        response_format=ToolStrategy(output, handle_errors=True),
        context_schema=StandardAgentContext,
        name="task-cleanup-investigator",
    )
