"""task-cleanup의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest

from ..runtime.checkpoint import GraphCheckpointProvider
from ..runtime.durable_graph import job_durability, with_thread
from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.budget import ExecutionBudget
from ..runtime.llm.client import make_chat
from ..runtime.llm.structured_agent import recursion_config
from ..runtime.node import NodeRegistry
from ..runtime.pricing import ModelRates
from ..runtime.telemetry.disclosure import TraceSafeMetadata
from ..runtime.tracer_client import TracerApiClient
from ..runtime.validation_graph import ValidationGraphContext
from ..shared.prompt_source_port import AgentPrompt
from .graph import TASK_CLEANUP_GRAPH, TASK_CLEANUP_NODE_NAMES
from .nodes.decision import InvestigateNode, RepairNode, ValidateDecisionsNode
from .nodes.inspect import InspectNode, TriageNode
from .nodes.result import EmptyNode, FinalizeNode
from .policy import build_routes
from .prompts import build_prompt_bundle
from .reader import CleanupLedgerReader

AGENT_NAME = "task-cleanup"


async def run_task_cleanup(
    req: TaskCleanupRequest,
    tracer: TracerApiClient,
    usage: ExecutionTrace,
    prompt: AgentPrompt,
    checkpoints: GraphCheckpointProvider | None = None,
) -> dict[str, Any]:
    """task-cleanup 노드를 실행 의존성과 결합해 그래프를 수행한다."""
    # 열쇠를 모르면 이어받을 자리가 없으므로 그 실행은 보존하지 않는다.
    resume_key = req.jobId
    saver = None if checkpoints is None or resume_key is None else await checkpoints.saver()
    prompts = build_prompt_bundle(prompt)
    chat = make_chat(
        req.model,
        req.apiKey,
        req.deadlineMs,
        feature_max_output_tokens=req.limits.maxOutputTokens,
    )
    fallback_model = req.effective_fallback_model()
    fallback_chat = (
        make_chat(
            fallback_model,
            req.apiKey,
            req.deadlineMs,
            feature_max_output_tokens=req.limits.maxOutputTokens,
        )
        if fallback_model is not None
        else None
    )
    reader = CleanupLedgerReader(tracer)
    budget = ExecutionBudget(req.limits.budgetUsd, ModelRates(req.modelRates))
    context = ValidationGraphContext(
        AGENT_NAME,
        usage,
        NodeRegistry(
            {
                TriageNode.name: TriageNode(
                    req,
                    reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["triageSystemPrompt"],
                ),
                InspectNode.name: InspectNode(
                    req,
                    reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["inspectSystemPrompt"],
                ),
                InvestigateNode.name: InvestigateNode(
                    req,
                    reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["investigatorSystemPrompt"],
                    repair_directive=prompts["repairDirective"],
                    language_directives=prompt.language_directives,
                ),
                ValidateDecisionsNode.name: ValidateDecisionsNode(usage),
                RepairNode.name: RepairNode(
                    req,
                    reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["investigatorSystemPrompt"],
                    repair_directive=prompts["repairDirective"],
                    language_directives=prompt.language_directives,
                ),
                FinalizeNode.name: FinalizeNode(),
                EmptyNode.name: EmptyNode(),
            },
            TASK_CLEANUP_NODE_NAMES,
        ),
        build_routes(usage, ValidateDecisionsNode.name),
    )
    final = await TASK_CLEANUP_GRAPH.compiled(saver).ainvoke(
        {
            "scanned_at": req.scannedAt,
            "language": req.language,
            "max_suggestions": req.maxSuggestions,
            "messages": [],
            "plan": None,
            "redispatch": None,
            "redispatch_ceiling": 0.0,
            "redispatch_count": 0,
            "reports": [],
            "exposed_candidates": {},
            "event_ids_by_task": {},
            "model_cost_usd": 0.0,
            "max_cost_usd": req.limits.budgetUsd,
            "suggestions": [],
            "validation_errors": [],
            "repair_attempted": False,
            "result": None,
        },
        context=context,
        config=_execution_config(
            30,
            TraceSafeMetadata(
                agent_name=AGENT_NAME,
                model_requested=req.model,
                prompt_version=prompt.version(),
                job_id=req.jobId,
            ),
            resume_key,
        ),
        durability=job_durability(saver),
    )
    return final["result"] or {"suggestions": []}


def _execution_config(limit: int, trace: TraceSafeMetadata, thread_id: str | None) -> RunnableConfig:
    """재개할 실행은 잡 하나를 열쇠로 삼고, 열쇠를 모르는 실행은 보존하지 않는다."""
    config = recursion_config(limit, trace)
    return config if thread_id is None else with_thread(config, thread_id)
