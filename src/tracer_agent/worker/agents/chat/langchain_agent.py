"""chat의 표준 LangChain 대화 agent를 자유 텍스트 출력과 함께 조립한다."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.prompt_cache import PromptCacheMiddleware
from ..runtime.llm.retry import model_retry_middleware, tool_retry_middleware
from ..runtime.llm.standard_agent import (
    StandardAgentContext,
    StandardAgentMiddleware,
    context_editing_middleware,
)
from ..runtime.llm.turn_limit import TurnLimitMiddleware


def chat_middleware(
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
) -> list[AgentMiddleware[Any, Any, Any]]:
    """chat agent의 미들웨어를 안쪽부터 바깥쪽 순서로 세운다."""
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        # error로 끊으면 그때까지의 답변과 도구 결과를 통째로 잃어 SDK 백엔드와 결과가 나뉜다.
        TurnLimitMiddleware(run_limit=max_turns, exit_behavior="end"),
        # 장부가 정리 뒤의 토큰을 세도록 StandardAgentMiddleware보다 앞에 둔다.
        context_editing_middleware(),
        StandardAgentMiddleware(serialize_tools=True),
        # 남은 몫을 알리는 꼬리가 붙은 뒤에 서야 경계를 그 꼬리 앞에 놓을 수 있다.
        PromptCacheMiddleware(ttl="1h"),
        tool_retry_middleware(transient_errors),
    ]
    # 재시도가 더 안쪽이어야 같은 모델로 소진된 뒤에만 FallbackModelMiddleware가 대체로 넘어간다.
    if fallback_chat is not None:
        middleware.append(FallbackModelMiddleware(fallback_chat))
    middleware.append(model_retry_middleware())
    return middleware


def build_chat_agent(
    chat: BaseChatModel,
    system_prompt: str,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """도구 실행과 자유 텍스트 응답을 갖춘 chat 대화 agent를 스레드·사용자 기억과 함께 컴파일한다."""
    system = SystemMessage(content=system_prompt)
    middleware = chat_middleware(transient_errors, max_turns=max_turns, fallback_chat=fallback_chat)
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=system,
        middleware=middleware,
        context_schema=StandardAgentContext,
        checkpointer=checkpointer,
        store=store,
        name="chat-conversation",
    )
