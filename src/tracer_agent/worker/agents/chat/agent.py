"""chat 대화 에이전트의 실행 의존성과 그래프 노드를 조립해 접수된 실행을 수행한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from tracer_agent.shared.agents.chat.models import ChatRequest, ChatResult, ChatState

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.client import make_chat
from ..runtime.llm.structured_agent import recursion_config, recursion_limit_for
from ..runtime.node import node_registry
from ..runtime.telemetry.disclosure import TraceSafeMetadata
from ..runtime.validation_graph import FINALIZE, ValidationGraphContext
from .checkpoint import ChatCheckpointProvider
from .drafts import DraftPublisher
from .graph import CHAT_GRAPH
from .nodes.converse import ConverseNode
from .prompts import PROMPT_VERSION, build_system_prompt

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
    checkpoints: ChatCheckpointProvider | None = None,
    drafts: DraftPublisher | None = None,
    prompt_fragments: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> ConverseNode:
    tokens = req.limits.maxOutputTokens
    chat = make_chat(req.model, req.apiKey, req.deadlineMs, max_output_tokens=tokens, streaming=streaming)
    fallback_model = req.effective_fallback_model()
    fallback_chat = (
        make_chat(fallback_model, req.apiKey, req.deadlineMs, max_output_tokens=tokens, streaming=streaming)
        if fallback_model is not None
        else None
    )
    return ConverseNode(
        req,
        http_client,
        checkpoints,
        usage,
        chat,
        fallback_chat,
        agent_name=AGENT_NAME,
        drafts=drafts,
        system_prompt=build_system_prompt(prompt_fragments),
    )


def _initial_state(req: ChatRequest) -> ChatState:
    return {
        "language": req.language,
        "summary": req.summary,
        "facts": req.facts,
        "messages": [],
        "model_cost_usd": 0.0,
        "result": None,
    }


async def run_chat(
    req: ChatRequest,
    http_client: httpx.AsyncClient,
    usage: ExecutionTrace,
    checkpoints: ChatCheckpointProvider | None = None,
    prompt_fragments: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """chat 노드를 실행 의존성과 결합해 대화 그래프를 수행한다."""
    # 창구가 있으면 진행 중인 답변을 보내야 하므로 토큰이 흐르는 모델로 조립한다.
    drafts = None if req.draftCallback is None else DraftPublisher(http_client, req.draftCallback)
    node = _build_node(
        req,
        http_client,
        usage,
        streaming=drafts is not None,
        checkpoints=checkpoints,
        drafts=drafts,
        prompt_fragments=prompt_fragments,
    )
    context = ValidationGraphContext(AGENT_NAME, usage, node_registry([node]), _no_validation)
    final = await CHAT_GRAPH.ainvoke(
        _initial_state(req),
        context=context,
        config=recursion_config(
            recursion_limit_for(req.limits.maxTurns),
            TraceSafeMetadata(
                agent_name=AGENT_NAME,
                model_requested=req.model,
                prompt_version=PROMPT_VERSION,
                job_id=req.jobId,
                execution_id=req.executionId,
                attempt_id=None if req.draftCallback is None else str(req.draftCallback.attempt),
            ),
        ),
    )
    return final["result"] or ChatResult().model_dump(mode="json")
