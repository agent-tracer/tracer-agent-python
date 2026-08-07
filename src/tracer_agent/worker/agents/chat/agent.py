"""chat 대화 에이전트의 실행 의존성과 그래프 노드를 조립해 접수된 실행을 수행한다."""

from __future__ import annotations

from typing import Any

import httpx

from tracer_agent.shared.agents.chat.models import ChatRequest, ChatResult, initial_chat_state

from ..runtime.checkpoint import GraphCheckpointProvider
from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.client import ChatPair, make_chat_pair
from ..runtime.llm.structured_agent import recursion_config, recursion_limit_for
from ..runtime.node import NodeRegistry
from ..runtime.routes import FINALIZE
from ..runtime.telemetry.disclosure import TraceSafeMetadata
from ..runtime.validation_graph import ValidationGraphContext
from ..shared.prompt_source_port import AgentPrompt
from .drafts import DraftPublisher, DraftSink, NullDraftPublisher
from .graph import CHAT_GRAPH, CHAT_NODE_NAMES
from .nodes.converse import ConverseNode
from .nodes.load_context import LoadContextNode
from .nodes.settle import SettleNode
from .prompts import build_system_prompt

AGENT_NAME = "chat"


# 대화는 검증 분기가 없어 라우터가 호출되지 않으므로 확정 경로만 돌려주는 자리표시자다.
def _no_validation(_state: Any) -> Any:
    return FINALIZE


def _build_node(
    req: ChatRequest,
    http_client: httpx.AsyncClient,
    usage: ExecutionTrace,
    *,
    streaming: bool,
    checkpoints: GraphCheckpointProvider | None = None,
    drafts: DraftSink,
    prompt: AgentPrompt,
    chats: ChatPair | None = None,
) -> ConverseNode:
    pair = chats or make_chat_pair(req, streaming=streaming)
    return ConverseNode(
        req,
        http_client,
        checkpoints,
        usage,
        pair.primary,
        pair.fallback,
        agent_name=AGENT_NAME,
        drafts=drafts,
        system_prompt=build_system_prompt(prompt),
        language_directives=prompt.language_directives,
    )


async def run_chat(
    req: ChatRequest,
    http_client: httpx.AsyncClient,
    usage: ExecutionTrace,
    prompt: AgentPrompt,
    checkpoints: GraphCheckpointProvider | None = None,
    chats: ChatPair | None = None,
) -> dict[str, Any]:
    """chat 노드를 실행 의존성과 결합해 대화 그래프를 수행한다."""
    # 대화는 토큰을 이어 받는 것이 본체이므로 창구 유무와 무관하게 스트리밍 모델로 조립한다.
    drafts: DraftSink = (
        NullDraftPublisher() if req.draftCallback is None else DraftPublisher(http_client, req.draftCallback)
    )
    node = _build_node(
        req,
        http_client,
        usage,
        streaming=True,
        checkpoints=checkpoints,
        drafts=drafts,
        prompt=prompt,
        chats=chats,
    )
    context = ValidationGraphContext(
        AGENT_NAME,
        usage,
        NodeRegistry(
            {
                LoadContextNode.name: LoadContextNode(req, http_client),
                ConverseNode.name: node,
                SettleNode.name: SettleNode(),
            },
            CHAT_NODE_NAMES,
        ),
        _no_validation,
    )
    final = await CHAT_GRAPH.ainvoke(
        initial_chat_state(req),
        context=context,
        config=recursion_config(
            recursion_limit_for(req.limits.maxTurns),
            TraceSafeMetadata(
                agent_name=AGENT_NAME,
                model_requested=req.model,
                prompt_version=prompt.version(),
                job_id=req.jobId,
                execution_id=req.executionId,
                attempt_id=None if req.draftCallback is None else str(req.draftCallback.attempt),
            ),
        ),
    )
    # 이 턴의 체크포인트는 액티비티 재시도만 막는 자리이며 대화의 정본은 원장이 갖는다.
    if checkpoints is not None:
        await checkpoints.forget(req.executionId)
    result: ChatResult = final["result"] or ChatResult()
    return result.model_dump(mode="json")
