"""턴마다 대화 agent를 다시 컴파일하지 않도록 같은 실행 조건의 판을 프로세스가 다시 쓴다."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from tracer_agent.shared.agents.chat.models import ChatRequest

from ..runtime.llm.client import ChatPair, make_chat_pair
from .langchain_agent import build_chat_agent
from .tools import chat_tool_registry

type CompiledChatAgent = CompiledStateGraph[Any, Any, Any, Any]

# 한 배포가 동시에 다루는 모델·사용자 조합만큼만 판을 들고 그 밖은 다시 컴파일한다.
_CACHED_AGENTS = 8


@dataclass(frozen=True)
class ChatAgentSpec:
    """컴파일된 대화 agent 하나를 정하는 값 전부이며 이 값이 같은 턴은 같은 판을 쓴다."""

    model: str
    fallback_model: str | None
    deadline_ms: int
    max_output_tokens: int
    max_turns: int
    # 판이 그 사용자의 자격에 매인 클라이언트를 들고 있으므로 자격이 다르면 판도 다르다.
    api_key_digest: str
    system_prompt: str
    tools: tuple[tuple[str, str], ...]

    @classmethod
    def of(cls, req: ChatRequest, system_prompt: str) -> ChatAgentSpec:
        """이 실행 봉투와 프롬프트가 요구하는 판을 가리키는 열쇠를 만든다."""
        return cls(
            model=req.model,
            fallback_model=req.effective_fallback_model(),
            deadline_ms=req.deadlineMs,
            max_output_tokens=req.limits.maxOutputTokens,
            max_turns=req.limits.maxTurns,
            api_key_digest=hashlib.sha256(req.apiKey.encode("utf-8")).hexdigest(),
            system_prompt=system_prompt,
            tools=tuple(sorted(req.toolDescriptions.items())),
        )


class ChatAgentSource(Protocol):
    """이 턴이 실행할 대화 agent를 내는 자리다."""

    def compiled(
        self, req: ChatRequest, system_prompt: str, saver: BaseCheckpointSaver[Any] | None
    ) -> CompiledChatAgent:
        """이 턴이 쓸 대화 agent를 낸다."""
        ...


def compile_chat_agent(
    chats: ChatPair, req: ChatRequest, system_prompt: str, saver: BaseCheckpointSaver[Any] | None
) -> CompiledChatAgent:
    """계약이 연 도구와 이 실행의 모델 짝으로 대화 agent 하나를 컴파일한다."""
    registry = chat_tool_registry(req.toolDescriptions)
    return build_chat_agent(
        chats.primary,
        system_prompt,
        registry.langchain_tools(),
        registry.transient_errors(),
        fallback_chat=chats.fallback,
        checkpointer=saver,
        max_turns=req.limits.maxTurns,
    )


class GivenModelChatAgents:
    """모델 짝을 받아 그 짝으로만 컴파일하며 다른 실행과 판을 나눠 쓰지 않는다."""

    def __init__(self, chats: ChatPair) -> None:
        self._chats = chats

    def compiled(
        self, req: ChatRequest, system_prompt: str, saver: BaseCheckpointSaver[Any] | None
    ) -> CompiledChatAgent:
        """받은 모델 짝으로 이 턴의 대화 agent를 컴파일한다."""
        return compile_chat_agent(self._chats, req, system_prompt, saver)


class CachedChatAgents:
    """같은 실행 조건과 같은 세이버의 턴이 컴파일한 판을 다시 쓰고 오래된 판부터 버린다."""

    def __init__(self, capacity: int = _CACHED_AGENTS) -> None:
        self._capacity = capacity
        self._compiled: OrderedDict[
            tuple[BaseCheckpointSaver[Any] | None, ChatAgentSpec], CompiledChatAgent
        ] = OrderedDict()

    def compiled(
        self, req: ChatRequest, system_prompt: str, saver: BaseCheckpointSaver[Any] | None
    ) -> CompiledChatAgent:
        """이 턴이 쓸 판을 내며 같은 열쇠의 판이 없을 때만 컴파일한다."""
        key = (saver, ChatAgentSpec.of(req, system_prompt))
        cached = self._compiled.get(key)
        if cached is not None:
            self._compiled.move_to_end(key)
            return cached
        # 컴파일이 await 없이 끝나므로 같은 이벤트 루프의 턴이 조회와 보관 사이에 끼어들지 못한다.
        agent = compile_chat_agent(make_chat_pair(req, streaming=True), req, system_prompt, saver)
        self._compiled[key] = agent
        if len(self._compiled) > self._capacity:
            self._compiled.popitem(last=False)
        return agent

    def size(self) -> int:
        """이 프로세스가 지금 들고 있는 판의 수다."""
        return len(self._compiled)


CHAT_AGENTS = CachedChatAgents()
