"""recipe-scan의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeScanRequest

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
from .graph import PROBE_WALL_CLOCK_CEILING_S, RECIPE_SCAN_GRAPH, RECIPE_SCAN_NODE_NAMES
from .nodes.candidate import InvestigateNode, RepairNode, ValidateCandidateNode
from .nodes.probe import ProbeNode
from .nodes.result import EmptyNode, FinalizeNode, wire_provenance
from .nodes.survey import SurveyNode
from .policy import build_routes
from .prompts import build_prompt_bundle
from .reader import RecipeLedgerReader
from .reservation import load_reservation_policy
from .search import RecipeSearchReader

AGENT_NAME = "recipe-scan"


async def run_recipe_scan(
    req: RecipeScanRequest,
    tracer: TracerApiClient,
    usage: ExecutionTrace,
    prompt: AgentPrompt,
    checkpoints: GraphCheckpointProvider | None = None,
) -> dict[str, Any]:
    """recipe-scan 노드를 실행 의존성과 결합해 그래프를 수행한다."""
    saver = None if checkpoints is None else await checkpoints.saver()
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
    reader = RecipeLedgerReader(tracer)
    search_reader = RecipeSearchReader(tracer)
    budget = ExecutionBudget(req.limits.budgetUsd, ModelRates(req.modelRates), max_turns=req.limits.maxTurns)
    # 뗄 순서를 바꾸면 그 시점 잔량에 곱하는 비율이 달라지므로 repair → survey → synthesisFloor 순서를 지킨다.
    policy = load_reservation_policy()
    repair_lease = budget.reserve(policy.repair.turns, policy.repair.budget_share)
    survey_lease = budget.reserve(policy.survey.turns, policy.survey.budget_share)
    synthesis_floor_lease = budget.reserve(policy.synthesis_floor.turns, policy.synthesis_floor.budget_share)
    context = ValidationGraphContext(
        AGENT_NAME,
        usage,
        NodeRegistry(
            {
                SurveyNode.name: SurveyNode(
                    req, usage, chat, fallback_chat, budget, survey_lease, prompts["surveySystemPrompt"]
                ),
                ProbeNode.name: ProbeNode(
                    req,
                    reader,
                    search_reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["probeSystemPrompt"],
                    wall_clock_ceiling_s=PROBE_WALL_CLOCK_CEILING_S,
                ),
                InvestigateNode.name: InvestigateNode(
                    req,
                    reader,
                    search_reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["investigatorSystemPrompt"],
                    repair_directive=prompts["repairDirective"],
                    language_directives=prompt.language_directives,
                    synthesis_floor_lease=synthesis_floor_lease,
                ),
                ValidateCandidateNode.name: ValidateCandidateNode(usage),
                RepairNode.name: RepairNode(
                    req,
                    reader,
                    search_reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["investigatorSystemPrompt"],
                    repair_directive=prompts["repairDirective"],
                    language_directives=prompt.language_directives,
                    lease=repair_lease,
                ),
                FinalizeNode.name: FinalizeNode(),
                EmptyNode.name: EmptyNode(),
            },
            RECIPE_SCAN_NODE_NAMES,
        ),
        build_routes(usage, ValidateCandidateNode.name),
    )
    final = await RECIPE_SCAN_GRAPH.compiled(saver).ainvoke(
        {
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
            None if saver is None else req.jobId,
        ),
        durability=job_durability(saver),
    )
    return final["result"] or {"recipes": [], "provenance": wire_provenance(ProvenanceCatalog())}


def _execution_config(limit: int, trace: TraceSafeMetadata, thread_id: str | None) -> RunnableConfig:
    """재개할 실행은 잡 하나를 열쇠로 삼고, 보존하지 않는 실행은 열쇠 없이 돈다."""
    config = recursion_config(limit, trace)
    return config if thread_id is None else with_thread(config, thread_id)
