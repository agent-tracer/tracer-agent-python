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
    ChatFact,
    ChatHistoryMessage,
    ChatRequest,
    ChatResult,
    ChatState,
    ConverseUpdate,
    ProposedWrite,
)
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact_text

from ...runtime.checkpoint import GraphCheckpointProvider
from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import ToolLoopBudget
from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.llm.structured_agent import recursion_limit_for
from ...runtime.node import GraphNode
from ...runtime.pricing import ModelRates
from ..checkpointer import seed_checkpoint
from ..context import ChatContextReader, replay_messages
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
        result = ChatResult(
            assistantText=redact_text(_final_text(messages), stage=RedactionStage.OUTPUT),
            proposedWrites=prepared.proposals,
        )
        return {
            "messages": messages,
            "model_cost_usd": prepared.budget.spent,
            "result": result.model_dump(mode="json"),
        }

    async def _invoke(self, prepared: _PreparedTurn) -> list[Any]:
        raw: Any = await prepared.agent.ainvoke(
            {"messages": prepared.messages_in}, context=prepared.context, config=prepared.config
        )
        return raw["messages"] if isinstance(raw, dict) else []

    async def _invoke_with_drafts(self, prepared: _PreparedTurn, drafts: DraftPublisher) -> list[Any]:
        """접수만 하고 끊긴 실행이라 진행 중인 답변을 창구로 되돌려 보내며 돈다."""
        final_state: dict[str, Any] = {}
        async for mode, chunk in prepared.agent.astream(
            {"messages": prepared.messages_in},
            context=prepared.context,
            config=prepared.config,
            stream_mode=["messages", "values"],
        ):
            if mode == "messages":
                message = chunk[0]
                if isinstance(message, AIMessage):
                    await drafts.push(_text(message.content))
                    for call in message.tool_calls:
                        await drafts.push_tool(str(call.get("name", "")))
            elif mode == "values" and isinstance(chunk, dict):
                final_state = chunk
        await drafts.flush()
        messages: list[Any] = final_state.get("messages", [])
        return messages

    async def _prepare(self, state: ChatState) -> _PreparedTurn:
        proposals: list[ProposedWrite] = []
        messages, summary, facts = await self._context(state)
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
        context_prompt = build_context_prompt(self._language_directives[state["language"]], summary, facts)
        messages_in = await self._seed(agent, checkpointer, config, messages, context_prompt)
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
        return self._with_context(seeded, context_prompt)

    @staticmethod
    def _with_context(messages: list[BaseMessage], context_prompt: str) -> list[BaseMessage]:
        """턴마다 바뀌는 지시와 요약과 사실을 human으로 꼬리에 두어 캐시 접두사도 system 자리도 지킨다."""
        if not context_prompt.strip():
            return messages
        return [*messages, HumanMessage(content=context_prompt)]

    async def _context(self, state: ChatState) -> tuple[list[ChatHistoryMessage], str | None, list[ChatFact]]:
        if not self._req.agentApiBaseUrl or self._req.messages:
            return self._req.messages, state["summary"], state["facts"]
        return await ChatContextReader(
            self._http_client,
            self._req.agentApiBaseUrl,
            self._req.userId,
            self._req.threadId,
            self._req.executionId,
            self._req.scopeToken or None,
        ).load()

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


def _final_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            text = _text(message.content)
            if text:
                return text
    return ""


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        return "".join(parts)
    return ""
