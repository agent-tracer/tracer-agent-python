"""title-suggestion의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from typing import Any

from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestionDraft,
    TitleSuggestionRequest,
)
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.checkpoint import GraphCheckpointProvider
from ..runtime.durable_graph import execution_config, job_durability, resume_input
from ..runtime.execution.trace import ExecutionTrace
from ..runtime.job_agent import JobAgent
from ..runtime.llm.budget import ExecutionBudget
from ..runtime.llm.client import ChatPair, make_chat_pair
from ..runtime.node import NodeRegistry
from ..runtime.pricing import ModelRates
from ..runtime.telemetry.disclosure import TraceSafeMetadata
from ..runtime.tracer_client import TracerApiPort
from ..runtime.validation_graph import ValidationGraphContext
from ..shared.prompt_source_port import AgentPrompt
from .deps import TitleDeps
from .graph import TITLE_SUGGESTION_GRAPH, TITLE_SUGGESTION_NODE_NAMES
from .nodes.candidate import (
    EmptyNode,
    FinalizeNode,
    InvestigateNode,
    RepairNode,
    ValidateCandidateNode,
)
from .policy import build_routes
from .prompts import build_prompt_bundle
from .reader import TitleLedgerReader, load_title_context

# 조사 한 번과 수리 한 번이 도구를 진행 중인 동안 그래프가 밟는 노드 수의 상한이다.
_RECURSION_LIMIT = 20


async def prepare_title_suggestion(payload: dict[str, Any], tracer: TracerApiPort) -> TitleSuggestionRequest:
    """접수가 대화 발췌를 싣지 않았으면 이 시점에 스스로 조립해 요청을 세운다."""
    if "context" not in payload:
        context = await load_title_context(tracer, payload["taskId"])
        payload = {**payload, "context": context.model_dump(mode="json")}
    return TitleSuggestionRequest.model_validate(payload)


async def run_title_suggestion(
    req: TitleSuggestionRequest,
    tracer: TracerApiPort,
    usage: ExecutionTrace,
    prompt: AgentPrompt,
    checkpoints: GraphCheckpointProvider | None = None,
    chats: ChatPair | None = None,
) -> dict[str, Any]:
    """title-suggestion 노드를 실행 의존성과 결합해 그래프를 수행한다."""
    # 열쇠를 모르면 이어받을 자리가 없으므로 그 실행은 보존하지 않는다.
    resume_key = req.executionId or req.jobId
    saver = None if checkpoints is None or resume_key is None else await checkpoints.saver()
    deps = TitleDeps(
        req=req,
        reader=TitleLedgerReader(tracer),
        usage=usage,
        chats=chats or make_chat_pair(req),
        budget=ExecutionBudget(req.limits.budgetUsd, ModelRates(req.modelRates)),
        prompts=build_prompt_bundle(prompt),
        language_directives=prompt.language_directives,
    )
    context = ValidationGraphContext(
        AgentJobKind.TITLE_SUGGESTION,
        usage,
        NodeRegistry(
            {
                InvestigateNode.name: InvestigateNode(deps),
                ValidateCandidateNode.name: ValidateCandidateNode(usage),
                RepairNode.name: RepairNode(deps),
                FinalizeNode.name: FinalizeNode(),
                EmptyNode.name: EmptyNode(),
            },
            TITLE_SUGGESTION_NODE_NAMES,
        ),
        build_routes(usage, ValidateCandidateNode.name),
    )
    graph = TITLE_SUGGESTION_GRAPH.compiled(saver)
    config = execution_config(
        _RECURSION_LIMIT,
        TraceSafeMetadata(
            agent_name=AgentJobKind.TITLE_SUGGESTION,
            model_requested=req.model,
            prompt_version=prompt.version(),
            job_id=req.jobId,
        ),
        resume_key,
    )
    initial: dict[str, Any] = {
        "task_id": req.taskId,
        "language": req.language,
        "context": req.context,
        "messages": [],
        "model_cost_usd": 0.0,
        "candidate": None,
        "validation_errors": [],
        "repair_attempted": False,
        "result": None,
    }
    final = await graph.ainvoke(
        await resume_input(graph, config, initial, saver),
        context=context,
        config=config,
        durability=job_durability(saver),
    )
    result: TitleSuggestionDraft = final["result"] or TitleSuggestionDraft()
    return result.model_dump(mode="json")


TITLE_SUGGESTION_JOB = JobAgent(
    kind=AgentJobKind.TITLE_SUGGESTION,
    prepare=prepare_title_suggestion,
    run=run_title_suggestion,
)
