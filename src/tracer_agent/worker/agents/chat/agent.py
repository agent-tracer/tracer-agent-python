"""chat 대화 에이전트의 실행 의존성을 조립해 접수된 실행의 세 단계를 차례로 수행한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import httpx

from tracer_agent.shared.agents.chat.models import ChatRequest, ChatResult, ChatState, initial_chat_state
from tracer_agent.shared.agents.envelope.catalog import CATALOG
from tracer_agent.shared.agents.shared.model_tiering import CHAT_KIND

from ..runtime.checkpoint import GraphCheckpointProvider
from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.client import ChatPair
from ..runtime.timeouts import deadline_fraction_s
from ..shared.prompt_source_port import AgentPrompt
from .agent_cache import CHAT_AGENTS, ChatAgentSource, GivenModelChatAgents
from .backends import ChatTurnBackends
from .drafts import DraftPublisher, DraftSink, NullDraftPublisher
from .prompts import build_system_prompt
from .steps import (
    CONTEXT_ATTEMPTS,
    CONTEXT_TRANSIENT_ERRORS,
    CONVERSE_DEADLINE_SHARE,
    CONVERSE_STEP,
    LOAD_CONTEXT_STEP,
    SETTLE_STEP,
    ChatTurnSteps,
    ConverseStep,
    LoadContextStep,
    SettleStep,
)

AGENT_NAME = "chat"

_CONVERSE_TIMEOUT_S = deadline_fraction_s(CATALOG[CHAT_KIND].deadline_ms, CONVERSE_DEADLINE_SHARE)


async def run_chat(
    req: ChatRequest,
    http_client: httpx.AsyncClient,
    usage: ExecutionTrace,
    prompt: AgentPrompt,
    checkpoints: GraphCheckpointProvider | None = None,
    chats: ChatPair | None = None,
) -> dict[str, Any]:
    """대화 한 턴의 문맥 적재와 도구 루프와 종결을 차례로 수행해 결과 계약을 낸다."""
    # 대화는 토큰을 이어 받는 것이 본체이므로 창구 유무와 무관하게 스트리밍 모델로 조립한다.
    drafts: DraftSink = (
        NullDraftPublisher() if req.draftCallback is None else DraftPublisher(http_client, req.draftCallback)
    )
    agents: ChatAgentSource = CHAT_AGENTS if chats is None else GivenModelChatAgents(chats)
    converse = ConverseStep(
        req,
        ChatTurnBackends.of(req, http_client),
        checkpoints,
        usage,
        agents,
        agent_name=AGENT_NAME,
        drafts=drafts,
        system_prompt=build_system_prompt(prompt),
        prompt_version=prompt.version(),
        tool_contract_version=prompt.tool_contract_version,
        language_directives=prompt.language_directives,
    )
    load_context = LoadContextStep(req, http_client)
    steps = ChatTurnSteps(AGENT_NAME, usage)
    state: ChatState = initial_chat_state(req)

    loaded = await steps.run(
        LOAD_CONTEXT_STEP,
        lambda: load_context.run(state),
        attempts=CONTEXT_ATTEMPTS,
        retry_on=CONTEXT_TRANSIENT_ERRORS,
    )
    state = _advanced(state, loaded)
    conversed = await steps.run(CONVERSE_STEP, lambda: converse.run(state), timeout_s=_CONVERSE_TIMEOUT_S)
    state = _advanced(state, conversed)
    settled = await steps.run(SETTLE_STEP, lambda: SettleStep().run(state))
    state = _advanced(state, settled)

    # 이 턴의 체크포인트는 액티비티 재시도만 막는 자리이며 대화의 정본은 원장이 갖는다.
    if checkpoints is not None:
        await checkpoints.forget(req.executionId)
    result: ChatResult = state["result"] or ChatResult()
    return result.model_dump(mode="json")


def _advanced(state: ChatState, update: Mapping[str, Any]) -> ChatState:
    """단계가 갱신한 칸만 이 턴의 상태에 옮긴다."""
    # 갱신 타입은 상태의 부분집합이지만 TypedDict 합성은 정적으로 표현되지 않는다.
    return cast("ChatState", {**state, **update})
