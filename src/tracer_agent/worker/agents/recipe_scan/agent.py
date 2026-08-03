"""recipe-scan의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from typing import Any

from tracer_agent.shared.agents.recipe_scan.models import (
    ProvenanceCatalog,
    RecipeScanRequest,
    RecipeScanResult,
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
from .deps import RecipeDeps
from .graph import PROBE_WALL_CLOCK_CEILING_S, RECIPE_SCAN_GRAPH, RECIPE_SCAN_NODE_NAMES
from .nodes.candidate import InvestigateNode, RepairNode, ValidateCandidateNode
from .nodes.probe import ProbeNode
from .nodes.result import EmptyNode, FinalizeNode, wire_provenance
from .nodes.survey import SurveyNode
from .outputs import deliver_recipes
from .policy import build_routes
from .prompts import build_prompt_bundle
from .reader import RecipeLedgerReader
from .reservation import load_reservation_policy
from .search import RecipeSearchReader

# 조사 계획과 전문가 팬아웃과 종합과 수리가 진행 중인 동안 그래프가 밟는 노드 수의 상한이다.
_RECURSION_LIMIT = 30


async def prepare_recipe_scan(payload: dict[str, Any], _tracer: TracerApiPort) -> RecipeScanRequest:
    """접수가 실은 값만으로 스캔 요청을 세운다."""
    return RecipeScanRequest.model_validate(payload)


async def run_recipe_scan(
    req: RecipeScanRequest,
    tracer: TracerApiPort,
    usage: ExecutionTrace,
    prompt: AgentPrompt,
    checkpoints: GraphCheckpointProvider | None = None,
    chats: ChatPair | None = None,
) -> dict[str, Any]:
    """recipe-scan 노드를 실행 의존성과 결합해 그래프를 수행한다."""
    # 열쇠를 모르면 이어받을 자리가 없으므로 그 실행은 보존하지 않는다.
    resume_key = req.executionId or req.jobId
    saver = None if checkpoints is None or resume_key is None else await checkpoints.saver()
    budget = ExecutionBudget(req.limits.budgetUsd, ModelRates(req.modelRates), max_turns=req.limits.maxTurns)
    # 뗄 순서를 바꾸면 그 시점 잔량에 곱하는 비율이 달라지므로 repair → survey → synthesisFloor 순서를 지킨다.
    policy = load_reservation_policy()
    repair_lease = budget.reserve(policy.repair.turns, policy.repair.budget_share)
    survey_lease = budget.reserve(policy.survey.turns, policy.survey.budget_share)
    synthesis_floor_lease = budget.reserve(policy.synthesis_floor.turns, policy.synthesis_floor.budget_share)
    deps = RecipeDeps(
        req=req,
        reader=RecipeLedgerReader(tracer),
        search=RecipeSearchReader(tracer),
        usage=usage,
        chats=chats or make_chat_pair(req),
        budget=budget,
        prompts=build_prompt_bundle(prompt),
        prompt=prompt,
        language_directives=prompt.language_directives,
    )
    context = ValidationGraphContext(
        AgentJobKind.RECIPE_SCAN,
        usage,
        NodeRegistry(
            {
                SurveyNode.name: SurveyNode(deps, survey_lease),
                ProbeNode.name: ProbeNode(deps, PROBE_WALL_CLOCK_CEILING_S),
                InvestigateNode.name: InvestigateNode(deps, synthesis_floor_lease),
                ValidateCandidateNode.name: ValidateCandidateNode(usage),
                RepairNode.name: RepairNode(deps, repair_lease),
                FinalizeNode.name: FinalizeNode(),
                EmptyNode.name: EmptyNode(),
            },
            RECIPE_SCAN_NODE_NAMES,
        ),
        build_routes(usage, ValidateCandidateNode.name),
    )
    graph = RECIPE_SCAN_GRAPH.compiled(saver)
    config = execution_config(
        _RECURSION_LIMIT,
        TraceSafeMetadata(
            agent_name=AgentJobKind.RECIPE_SCAN,
            model_requested=req.model,
            prompt_version=prompt.version(),
            job_id=req.jobId,
        ),
        resume_key,
    )
    initial: dict[str, Any] = {
        "task_id": req.taskId,
        "language": req.language,
        "user_prompt": req.userPrompt,
        "messages": [],
        "plan": None,
        "redispatch": None,
        "redispatch_count": 0,
        "reports": [],
        "provenance": ProvenanceCatalog(),
        "model_cost_usd": 0.0,
        "max_cost_usd": budget.remaining_budget_usd,
        "max_turns": budget.remaining_turns,
        "model_turns_used": 0,
        "candidates": [],
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
    result: RecipeScanResult = final["result"] or RecipeScanResult(
        provenance=wire_provenance(ProvenanceCatalog())
    )
    return result.model_dump(mode="json", exclude_none=True)


RECIPE_SCAN_JOB = JobAgent(
    kind=AgentJobKind.RECIPE_SCAN,
    prepare=prepare_recipe_scan,
    run=run_recipe_scan,
    deliver=deliver_recipes,
)
