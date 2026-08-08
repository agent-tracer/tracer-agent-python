"""chat의 표준 LangChain 대화 agent를 자유 텍스트 출력과 함께 조립한다."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from ..runtime.llm.middleware_stack import AgentMiddlewareStack
from .tools import ChatToolContext


def chat_stack(
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
) -> AgentMiddlewareStack:
    """대화는 구조화 출력을 요구하지 않으므로 산출을 다시 받는 층만 세우지 않는다."""
    return AgentMiddlewareStack(
        max_turns=max_turns,
        transient_errors=transient_errors,
        fallback_chat=fallback_chat,
        serializes_tools=True,
        ends_on_turn_limit=True,
    )


def build_chat_agent(
    chat: BaseChatModel,
    system_prompt: str,
    tools: list[BaseTool],
    transient_errors: tuple[type[Exception], ...],
    *,
    max_turns: int,
    fallback_chat: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """도구 실행과 자유 텍스트 응답을 갖춘 chat 대화 agent를 이 턴의 단기기억과 함께 컴파일한다."""
    return create_agent(
        chat,
        tools=list(tools),
        system_prompt=SystemMessage(content=system_prompt),
        middleware=chat_stack(transient_errors, max_turns=max_turns, fallback_chat=fallback_chat).build(),
        # 요청별 진입점은 이 컨텍스트가 실어 나르므로 컴파일한 판이 특정 실행에 매이지 않는다.
        context_schema=ChatToolContext,
        checkpointer=checkpointer,
        name="chat-conversation",
    )
