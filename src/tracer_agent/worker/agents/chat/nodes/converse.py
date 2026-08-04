"""chat의 대화 노드가 도구 루프를 돌려 어시스턴트 답변과 확인 대기 행 인용을 낸다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from tracer_agent.shared.agents.chat.models import (
    ChatHistoryMessage,
    ChatRequest,
    ChatState,
    ConverseUpdate,
    ProposedWrite,
)

from ...runtime.checkpoint import GraphCheckpointProvider
from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import ToolLoopBudget
from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.llm.structured_agent import recursion_limit_for
from ...runtime.llm.trajectory import step_content_text
from ...runtime.node import GraphNode
from ...runtime.pricing import ModelRates
from ..checkpointer import seed_checkpoint
from ..context import replay_messages
from ..drafts import DraftPublisher
from ..langchain_agent import build_chat_agent
from ..memory import ChatMemoryClient
from ..prompts import build_context_prompt
from ..reader import ChatReadClient
from ..store import ChatMemoryStore
from ..tools import build_chat_registry
from ..writer import ChatWriteClient

# 읽기 도구는 HTTP만 타므로 연결 계열 오류만 일시적이며 도메인 응답은 재시도하지 않는다.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    httpx.TransportError,
    ConnectionError,
    TimeoutError,
)


class ConverseNode(GraphNode[ChatState, ConverseUpdate]):
    """대화 이력과 도구로 한 턴의 어시스턴트 답변을 만든다."""

    name = "converse"

    def __init__(
        self,
        req: ChatRequest,
        http_client: httpx.AsyncClient,
        checkpoints: GraphCheckpointProvider | None,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        *,
        agent_name: str,
        drafts: DraftPublisher | None = None,
        system_prompt: str,
        language_directives: Mapping[str, str],
    ) -> None:
        self._req = req
        self._http_client = http_client
        self._checkpoints = checkpoints
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._agent_name = agent_name
        self._drafts = drafts
        self._system_prompt = system_prompt
        self._language_directives = language_directives

    async def run(self, state: ChatState) -> ConverseUpdate:
        prepared = await self._prepare(state)
        messages = (
            await self._invoke(prepared)
            if self._drafts is None
            else await self._invoke_with_drafts(prepared, self._drafts)
        )
        return {
            "messages": messages,
            "model_cost_usd": prepared.budget.delta,
            "model_turns_used": sum(1 for message in messages if isinstance(message, AIMessage)),
            "proposals": prepared.proposals,
        }

    async def _invoke(self, prepared: _PreparedTurn) -> list[BaseMessage]:
        raw = await prepared.agent.ainvoke(
            {"messages": prepared.messages_in}, context=prepared.context, config=prepared.config
        )
        return _messages_of(raw.get("messages")) if isinstance(raw, dict) else []

    async def _invoke_with_drafts(self, prepared: _PreparedTurn, drafts: DraftPublisher) -> list[BaseMessage]:
        """접수만 하고 끊긴 실행이라 진행 중인 답변을 창구로 되돌려 보내며 수행한다."""
        collected: list[BaseMessage] = []
        # 한 도구 호출이 여러 조각에 걸쳐 오므로 이미 알린 호출을 여기서 기억한다.
        announced: set[str] = set()
        async for mode, chunk in prepared.agent.astream(
            {"messages": prepared.messages_in},
            context=prepared.context,
            config=prepared.config,
            # 상태 전체를 매 스텝 실어 나르지 않도록 그 스텝이 더한 것만 받는다.
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                message = chunk[0]
                if isinstance(message, AIMessage):
                    self._usage.mark_first_token()
                    await drafts.push(step_content_text(message.content))
                    for name in _fresh_tool_names(message, announced):
                        await drafts.push_tool(name)
            elif mode == "updates" and isinstance(chunk, dict):
                collected.extend(_appended_messages(chunk))
        await drafts.flush()
        return collected

    async def _prepare(self, state: ChatState) -> _PreparedTurn:
        proposals: list[ProposedWrite] = []
        checkpointer = await self._checkpointer()
        registry = build_chat_registry(
            self._read_client(),
            proposals,
            self._req.toolDescriptions,
            agent_name=self._agent_name,
            write_client=self._write_client(),
            agent_read_client=self._agent_read_client(),
        )
        agent = build_chat_agent(
            self._chat,
            self._system_prompt,
            registry.langchain_tools(),
            TRANSIENT_ERRORS,
            fallback_chat=self._fallback_chat,
            checkpointer=checkpointer,
            store=self._memory_store(),
            max_turns=self._req.limits.maxTurns,
        )
        budget = ToolLoopBudget(
            self._agent_name,
            self._req.model,
            self._req.limits.budgetUsd,
            ModelRates(self._req.modelRates),
            state["model_cost_usd"],
        )
        context = StandardAgentContext(
            agent_name=self._agent_name,
            trace=self._usage,
            budget=budget,
            max_model_turns=self._req.limits.maxTurns,
        )
        config = self._config()
        # 판이 바뀌기 전에 선 체크포인트에는 이 칸이 없으므로 없으면 실린 이력으로 되돌린다.
        history = state.get("history") or self._req.messages
        context_prompt = build_context_prompt(
            self._language_directives[state["language"]],
            state["summary"],
            state["facts"],
            bool(history) and history[-1].role == "tool",
        )
        messages_in = await self._seed(agent, checkpointer, config, history, context_prompt)
        return _PreparedTurn(agent, messages_in, config, context, budget, proposals)

    def _config(self) -> RunnableConfig:
        # thread_id는 체크포인터가 단기기억을 범위로 잡는 열쇠이며 실행 하나가 그 범위다.
        return {
            "recursion_limit": recursion_limit_for(self._req.limits.maxTurns),
            "configurable": {"thread_id": self._req.executionId},
        }

    async def _seed(
        self,
        agent: CompiledStateGraph[Any, Any, Any, Any],
        checkpointer: BaseCheckpointSaver[Any] | None,
        config: RunnableConfig,
        history: list[ChatHistoryMessage],
        context_prompt: str,
    ) -> list[BaseMessage]:
        replayed = replay_messages(history)
        if checkpointer is None:
            return self._with_context(replayed, context_prompt)
        seeded = await seed_checkpoint(agent, checkpointer, config, replayed)
        # 이어받는 시도의 체크포인트에는 앞선 시도가 붙인 꼬리가 이미 있어 다시 붙이면 두 벌이 된다.
        if seeded.resumed:
            return seeded.messages
        return self._with_context(seeded.messages, context_prompt)

    @staticmethod
    def _with_context(messages: list[BaseMessage], context_prompt: str) -> list[BaseMessage]:
        """턴마다 바뀌는 지시와 요약과 사실을 human으로 꼬리에 두어 캐시 접두사도 system 자리도 지킨다."""
        if not context_prompt.strip():
            return messages
        return [*messages, HumanMessage(content=context_prompt)]

    async def _checkpointer(self) -> BaseCheckpointSaver[Any] | None:
        if self._checkpoints is None:
            return None
        return await self._checkpoints.saver()

    def _read_client(self) -> ChatReadClient | None:
        if not self._req.readApiBaseUrl:
            return None
        return ChatReadClient(
            self._http_client,
            self._req.readApiBaseUrl,
            self._req.userId,
            self._req.scopeToken or None,
        )

    def _agent_read_client(self) -> ChatReadClient | None:
        # 잡 창구처럼 원장이 에이전트 서비스에 있는 읽기 도구는 추적이 아니라 이 기점을 부른다.
        if not self._req.agentApiBaseUrl:
            return None
        return ChatReadClient(
            self._http_client,
            self._req.agentApiBaseUrl,
            self._req.userId,
            self._req.scopeToken or None,
        )

    def _write_client(self) -> ChatWriteClient | None:
        if not self._req.agentApiBaseUrl:
            return None
        return ChatWriteClient(
            self._http_client,
            self._req.agentApiBaseUrl,
            self._req.userId,
            self._req.threadId,
            self._req.scopeToken or None,
        )

    def _memory_store(self) -> ChatMemoryStore | None:
        if not self._req.agentApiBaseUrl:
            return None
        return ChatMemoryStore(
            ChatMemoryClient(
                self._http_client,
                self._req.agentApiBaseUrl,
                self._req.userId,
                self._req.scopeToken or None,
            )
        )


@dataclass
class _PreparedTurn:
    """blocking·streaming 실행이 공유하는, 조립이 끝난 한 턴의 실행 재료다."""

    agent: CompiledStateGraph[Any, Any, Any, Any]
    messages_in: list[BaseMessage]
    config: RunnableConfig
    context: StandardAgentContext
    budget: ToolLoopBudget
    proposals: list[ProposedWrite]


def _fresh_tool_names(message: AIMessage, announced: set[str]) -> list[str]:
    """이 조각이 처음 드러낸 도구 호출의 이름만 내고 이미 알린 것은 거른다."""
    names: list[str] = []
    for call in message.tool_calls:
        name = str(call.get("name") or "")
        key = str(call.get("id") or name)
        if not key or key in announced:
            continue
        announced.add(key)
        names.append(name)
    return names


def _appended_messages(update: Mapping[str, object]) -> list[BaseMessage]:
    """한 슈퍼스텝의 갱신에서 이 턴이 새로 더한 메시지만 꺼낸다."""
    appended: list[BaseMessage] = []
    for node_update in update.values():
        if isinstance(node_update, dict):
            appended.extend(_messages_of(node_update.get("messages")))
    return appended


def _messages_of(value: object) -> list[BaseMessage]:
    """SDK가 실어 보낸 값에서 모델 메시지만 남긴다."""
    if not isinstance(value, list):
        return []
    return [message for message in value if isinstance(message, BaseMessage)]
