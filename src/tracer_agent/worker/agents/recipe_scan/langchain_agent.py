"""recipe-scan의 표준 LangChain agent를 도구 레지스트리로 컴파일한다."""

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

from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.prompt_cache import PromptCacheMiddleware
from ..runtime.llm.retry import model_retry_middleware, tool_retry_middleware
from ..runtime.llm.standard_agent import (
    StandardAgentContext,
    StandardAgentMiddleware,
    context_editing_middleware,
)


def recipe_middleware(
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
) -> list[AgentMiddleware[Any, Any, Any]]:
    """recipe-scan agent의 미들웨어를 안쪽부터 바깥쪽 순서로 세운다."""
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="error"),
        # 장부가 정리 뒤의 토큰을 세도록 StandardAgentMiddleware보다 앞에 둔다.
        context_editing_middleware(),
        StandardAgentMiddleware(),
        # 남은 몫을 알리는 꼬리가 붙은 뒤에 서야 경계를 그 꼬리 앞에 놓을 수 있다.
        PromptCacheMiddleware(ttl="1h"),
        tool_retry_middleware(transient_errors),
    ]
    # 재시도가 더 안쪽이어야 같은 모델로 소진된 뒤에만 FallbackModelMiddleware가 대체로 넘어간다.
    if fallback_chat is not None:
        middleware.append(FallbackModelMiddleware(fallback_chat))
    middleware.append(model_retry_middleware())
    return middleware


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
    system = SystemMessage(content=system_prompt)
    middleware = recipe_middleware(transient_errors, max_turns=max_turns, fallback_chat=fallback_chat)
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=system,
        middleware=middleware,
        response_format=ToolStrategy(output, handle_errors=True),
        context_schema=StandardAgentContext,
        name="recipe-scan-investigator",
    )
