"""recipe-scan의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeScanRequest

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.llm.budget import ExecutionBudget
from ..runtime.llm.client import make_chat
from ..runtime.llm.structured_agent import recursion_config
from ..runtime.node import node_registry
from ..runtime.pricing import ModelRates
from ..runtime.telemetry.disclosure import TraceSafeMetadata
from ..runtime.tracer_client import TracerApiClient
from ..runtime.validation_graph import ValidationGraphContext
from ..shared.prompt_runtime import resolve_execution_prompt_bundle
from .graph import RECIPE_SCAN_GRAPH
from .nodes.candidate import InvestigateNode, RepairNode, ValidateCandidateNode
from .nodes.probe import ProbeNode
from .nodes.result import EmptyNode, FinalizeNode, wire_provenance
from .nodes.survey import SurveyNode
from .policy import build_routes
from .prompts import PROMPT_VERSION, build_resolved_prompt_bundle
from .reader import RecipeLedgerReader
from .search import RecipeSearchReader

AGENT_NAME = "recipe-scan"


async def run_recipe_scan(
    req: RecipeScanRequest,
    tracer: TracerApiClient,
    usage: ExecutionTrace,
    prompt_fragments: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """recipe-scan 노드를 실행 의존성과 결합해 그래프를 수행한다."""
    prompts, _ = resolve_execution_prompt_bundle(
        req,
        prompt_fragments,
        build_resolved_prompt_bundle,
        {
            "investigatorSystemPrompt": "lan.recipe-scan.investigator.system",
            "probeSystemPrompt": "lan.recipe-scan.probe.system",
            "surveySystemPrompt": "lan.recipe-scan.survey.system",
            "repairDirective": "lan.recipe-scan.investigator.repair",
        },
    )
    chat = make_chat(
        req.model,
        req.apiKey,
        req.deadlineMs,
        max_output_tokens=req.limits.maxOutputTokens,
    )
    fallback_model = req.effective_fallback_model()
    fallback_chat = (
        make_chat(fallback_model, req.apiKey, req.deadlineMs, max_output_tokens=req.limits.maxOutputTokens)
        if fallback_model is not None
        else None
    )
    reader = RecipeLedgerReader(tracer)
    search_reader = RecipeSearchReader(tracer)
    budget = ExecutionBudget(req.limits.budgetUsd, ModelRates(req.modelRates))
    context = ValidationGraphContext(
        AGENT_NAME,
        usage,
        node_registry(
            [
                SurveyNode(req, usage, chat, budget, prompts["surveySystemPrompt"]),
                ProbeNode(
                    req,
                    reader,
                    search_reader,
                    usage,
                    chat,
                    fallback_chat,
                    budget,
                    agent_name=AGENT_NAME,
                    system_prompt=prompts["probeSystemPrompt"],
                ),
                InvestigateNode(
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
                ),
                ValidateCandidateNode(usage),
                RepairNode(
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
                ),
                FinalizeNode(),
                EmptyNode(),
            ]
        ),
        build_routes(usage, ValidateCandidateNode.name),
    )
    final = await RECIPE_SCAN_GRAPH.ainvoke(
        {
            "task_id": req.taskId,
            "language": req.language,
            "user_prompt": req.userPrompt,
            "messages": [],
            "plan": None,
            "redispatch": None,
            "redispatch_ceiling": 0.0,
            "redispatch_count": 0,
            "reports": [],
            "provenance": ProvenanceCatalog(),
            "model_cost_usd": 0.0,
            "max_cost_usd": req.limits.budgetUsd,
            "candidates": [],
            "validation_errors": [],
            "repair_attempted": False,
            "result": None,
        },
        context=context,
        config=recursion_config(
            30,
            TraceSafeMetadata(
                agent_name=AGENT_NAME,
                model_requested=req.model,
                prompt_version=PROMPT_VERSION,
                job_id=req.jobId,
            ),
        ),
    )
    return final["result"] or {"recipes": [], "provenance": wire_provenance(ProvenanceCatalog())}
