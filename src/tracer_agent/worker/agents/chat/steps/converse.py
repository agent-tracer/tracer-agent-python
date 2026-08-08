"""chat의 대화 단계가 도구 루프를 실행해 어시스턴트 답변과 확인 대기 행 인용을 낸다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from tracer_agent.shared.agents.chat.models import (
    ChatHistoryMessage,
    ChatRequest,
    ChatState,
    ConverseUpdate,
)

from ...runtime.checkpoint import GraphCheckpointProvider
from ...runtime.durable_graph import execution_config
from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import SharedToolLoopBudget, single_loop_budget
from ...runtime.llm.structured_agent import recursion_limit_for
from ...runtime.llm.trajectory import step_content_text
from ...runtime.pricing import ModelRates
from ...runtime.telemetry.disclosure import TraceSafeMetadata
from ..agent_cache import ChatAgentSource
from ..backends import ChatTurnBackends
from ..checkpointer import seed_checkpoint
from ..context import replay_messages
from ..drafts import DraftSink
from ..prompts import build_context_prompt
from ..tools import ChatToolContext

CONVERSE_STEP = "converse"

# 도구 루프는 실행 데드라인의 대부분을 쓰되 종결이 실행될 자리를 남긴다.
CONVERSE_DEADLINE_SHARE = 0.9


class ConverseStep:
    """대화 이력과 도구로 한 턴의 어시스턴트 답변을 만든다."""

    def __init__(
        self,
        req: ChatRequest,
        backends: ChatTurnBackends,
        checkpoints: GraphCheckpointProvider | None,
        usage: ExecutionTrace,
        agents: ChatAgentSource,
        *,
        agent_name: str,
        drafts: DraftSink,
        system_prompt: str,
        prompt_version: str,
        tool_contract_version: str,
        language_directives: Mapping[str, str],
    ) -> None:
        self._req = req
        self._backends = backends
        self._checkpoints = checkpoints
        self._usage = usage
        self._agents = agents
        self._agent_name = agent_name
        self._drafts = drafts
        self._system_prompt = system_prompt
        self._prompt_version = prompt_version
        self._tool_contract_version = tool_contract_version
        self._language_directives = language_directives

    async def run(self, state: ChatState) -> ConverseUpdate:
        prepared = await self._prepare(state)
        messages = await self._stream(prepared)
        return {
            "messages": messages,
            "model_cost_usd": prepared.budget.delta,
            "model_turns_used": sum(1 for message in messages if isinstance(message, AIMessage)),
            "proposals": prepared.context.proposals,
        }

    async def _stream(self, prepared: _PreparedTurn) -> list[BaseMessage]:
        """토큰을 받는 대로 수행하고 창구가 있으면 진행 중인 답변을 그리로 되돌려 보낸다."""
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
                    await self._drafts.push(step_content_text(message.content))
                    for name in _fresh_tool_names(message, announced):
                        await self._drafts.push_tool(name)
            elif mode == "updates" and isinstance(chunk, dict):
                appended = _appended_messages(chunk)
                # 도구 결과가 돌아왔으면 모델이 다시 생각하는 구간이라 표시를 옮긴다.
                if any(isinstance(message, ToolMessage) for message in appended):
                    await self._drafts.mark_phase("thinking")
                collected.extend(appended)
        await self._drafts.flush()
        return collected

    async def _prepare(self, state: ChatState) -> _PreparedTurn:
        checkpointer = await self._checkpointer()
        agent = self._agents.compiled(self._req, self._system_prompt, checkpointer)
        budget = single_loop_budget(
            self._agent_name,
            self._req.model,
            self._req.limits.budgetUsd,
            ModelRates(self._req.modelRates),
            state["model_cost_usd"],
        )
        context = ChatToolContext(
            agent_name=self._agent_name,
            trace=self._usage,
            budget=budget,
            max_model_turns=self._req.limits.maxTurns,
            tool_owner=self._agent_name,
            backends=self._backends,
            proposals=[],
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
        return _PreparedTurn(agent, messages_in, config, context, budget)

    def _config(self) -> RunnableConfig:
        """잡과 같은 헬퍼로 이 턴의 실행 설정을 세워 두 에이전트의 공개 수준이 갈리지 않게 한다."""
        # thread_id는 체크포인터가 단기기억을 범위로 잡는 열쇠이며 실행 하나가 그 범위다.
        return execution_config(
            recursion_limit_for(self._req.limits.maxTurns),
            TraceSafeMetadata(
                agent_name=self._agent_name,
                model_requested=self._req.model,
                prompt_version=self._prompt_version,
                tool_contract_version=self._tool_contract_version,
                job_id=self._req.jobId,
                execution_id=self._req.executionId,
                attempt_id=self._attempt_id(),
            ),
            self._req.executionId,
        )

    def _attempt_id(self) -> str | None:
        callback = self._req.draftCallback
        return None if callback is None else str(callback.attempt)

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


@dataclass
class _PreparedTurn:
    """조립이 끝난 한 턴의 실행 재료다."""

    agent: CompiledStateGraph[Any, Any, Any, Any]
    messages_in: list[BaseMessage]
    config: RunnableConfig
    context: ChatToolContext
    budget: SharedToolLoopBudget


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
