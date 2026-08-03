"""task-cleanup의 표준 LangChain agent를 도구 직렬화와 함께 조립한다."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from tracer_agent.shared.agents.task_cleanup.models import CleanupDraft

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.prompt_cache import PromptCacheMiddleware
from ..runtime.llm.retry import tool_retry_middleware
from ..runtime.llm.standard_agent import StandardAgentMiddleware
from .tools import CleanupToolContext


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
        ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="error"),
        StandardAgentMiddleware(serialize_tools=True),
        # 남은 몫을 알리는 꼬리가 붙은 뒤에 서야 경계를 그 꼬리 앞에 놓을 수 있다.
        PromptCacheMiddleware(ttl="1h"),
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
        context_schema=CleanupToolContext,
        name="task-cleanup-investigator",
    )
