"""chat의 표준 LangChain 대화 agent를 자유 텍스트 출력과 함께 조립한다."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from ..runtime.llm.fallback import FallbackModelMiddleware
from ..runtime.llm.standard_agent import StandardAgentContext, StandardAgentMiddleware


def system_blocks(system_prompt: str, context_prompt: str) -> list[str | dict[str, Any]]:
    """턴마다 바뀌는 컨텍스트를 캐시 경계 뒤 블록으로 밀어 불변 시스템 접두사만 캐시에 남긴다."""
    # 캐시는 접두사 일치라 요약이나 사실이 경계 앞에 섞이면 그 하나가 바뀔 때 시스템 전체가 무효가 된다.
    blocks: list[str | dict[str, Any]] = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    if context_prompt.strip():
        blocks.append({"type": "text", "text": context_prompt})
    return blocks


def _tool_retry(transient_errors: tuple[type[Exception], ...]) -> ToolRetryMiddleware:
    return ToolRetryMiddleware(
        max_retries=2,
        retry_on=transient_errors,
        on_failure="error",
        backoff_factor=2.0,
        initial_delay=0.5,
        jitter=False,
    )


def build_chat_agent(
    chat: BaseChatModel,
    system_prompt: str,
    context_prompt: str,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """도구 실행과 자유 텍스트 응답을 갖춘 chat 대화 agent를 스레드·사용자 기억과 함께 컴파일한다."""
    system = SystemMessage(content=system_blocks(system_prompt, context_prompt))
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        # error로 끊으면 그때까지의 답변과 도구 결과를 통째로 잃어 SDK 백엔드와 결과가 갈라진다.
        ModelCallLimitMiddleware(run_limit=max_turns + 2, exit_behavior="end"),
        StandardAgentMiddleware(serialize_tools=True),
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
        context_schema=StandardAgentContext,
        checkpointer=checkpointer,
        store=store,
        name="chat-conversation",
    )
