"""task-cleanup의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tracer_agent.shared.agents.task_cleanup.models import CleanupResult, TaskCleanupRequest
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.checkpoint import GraphCheckpointProvider
from ..runtime.durable_graph import execution_config, job_durability, resume_input
from ..runtime.execution.trace import ExecutionTrace
from ..runtime.job_agent import JobAgent
from ..runtime.llm.budget import ExecutionBudget
from ..runtime.llm.client import make_chat_pair
from ..runtime.node import NodeRegistry
from ..runtime.pricing import ModelRates
from ..runtime.telemetry.disclosure import TraceSafeMetadata
from ..runtime.tracer_client import TracerApiClient
from ..runtime.validation_graph import ValidationGraphContext
from ..shared.prompt_source_port import AgentPrompt
from .deps import CleanupDeps
from .graph import TASK_CLEANUP_GRAPH, TASK_CLEANUP_NODE_NAMES
from .nodes.decision import InvestigateNode, RepairNode, ValidateDecisionsNode
from .nodes.inspect import InspectNode, TriageNode
from .nodes.result import EmptyNode, FinalizeNode
from .outputs import deliver_suggestions
from .policy import build_routes
from .prompts import build_prompt_bundle
from .reader import CleanupLedgerReader, load_cleanup_batch

# 선별과 후보별 조사와 결정과 수리가 도는 동안 그래프가 밟는 노드 수의 상한이다.
_RECURSION_LIMIT = 30


async def prepare_task_cleanup(payload: dict[str, Any], tracer: TracerApiClient) -> TaskCleanupRequest:
    """접수가 후보 배치를 싣지 않았으면 이 시점에 스스로 조립해 요청을 세운다."""
    if "batch" not in payload:
        now = datetime.now(UTC)
        batch = await load_cleanup_batch(tracer, now)
        payload = {
            **payload,
            "batch": batch.model_dump(mode="json"),
            "scannedAt": now.isoformat(),
        }
    return TaskCleanupRequest.model_validate(payload)


async def run_task_cleanup(
    req: TaskCleanupRequest,
    tracer: TracerApiClient,
    usage: ExecutionTrace,
    prompt: AgentPrompt,
    checkpoints: GraphCheckpointProvider | None = None,
) -> dict[str, Any]:
    """task-cleanup 노드를 실행 의존성과 결합해 그래프를 수행한다."""
    # 열쇠를 모르면 이어받을 자리가 없으므로 그 실행은 보존하지 않는다.
    resume_key = req.executionId or req.jobId
    saver = None if checkpoints is None or resume_key is None else await checkpoints.saver()
    deps = CleanupDeps(
        req=req,
        reader=CleanupLedgerReader(tracer),
        usage=usage,
        chats=make_chat_pair(req),
        budget=ExecutionBudget(req.limits.budgetUsd, ModelRates(req.modelRates)),
        prompts=build_prompt_bundle(prompt),
        language_directives=prompt.language_directives,
    )
    context = ValidationGraphContext(
        AgentJobKind.TASK_CLEANUP,
        usage,
        NodeRegistry(
            {
                TriageNode.name: TriageNode(deps),
                InspectNode.name: InspectNode(deps),
                InvestigateNode.name: InvestigateNode(deps),
                ValidateDecisionsNode.name: ValidateDecisionsNode(usage),
                RepairNode.name: RepairNode(deps),
                FinalizeNode.name: FinalizeNode(),
                EmptyNode.name: EmptyNode(),
            },
            TASK_CLEANUP_NODE_NAMES,
        ),
        build_routes(usage, ValidateDecisionsNode.name),
    )
    graph = TASK_CLEANUP_GRAPH.compiled(saver)
    config = execution_config(
        _RECURSION_LIMIT,
        TraceSafeMetadata(
            agent_name=AgentJobKind.TASK_CLEANUP,
            model_requested=req.model,
            prompt_version=prompt.version(),
            job_id=req.jobId,
        ),
        resume_key,
    )
    initial: dict[str, Any] = {
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
    }
    final = await graph.ainvoke(
        await resume_input(graph, config, initial, saver),
        context=context,
        config=config,
        durability=job_durability(saver),
    )
    result: CleanupResult = final["result"] or CleanupResult()
    return result.model_dump(mode="json")


TASK_CLEANUP_JOB = JobAgent(
    kind=AgentJobKind.TASK_CLEANUP,
    prepare=prepare_task_cleanup,
    run=run_task_cleanup,
    deliver=deliver_suggestions,
)
