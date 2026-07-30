"""title-suggestion의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionRequest

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
from .graph import TITLE_SUGGESTION_GRAPH
from .nodes.candidate import (
    EmptyNode,
    FinalizeNode,
    InvestigateNode,
    RepairNode,
    ValidateCandidateNode,
)
from .policy import build_routes
from .prompts import PROMPT_VERSION, build_prompt_bundle
from .reader import TitleLedgerReader

AGENT_NAME = "title-suggestion"


async def run_title_suggestion(
    req: TitleSuggestionRequest,
    tracer: TracerApiClient,
    usage: ExecutionTrace,
    prompt_fragments: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """title-suggestion 노드를 실행 의존성과 결합해 그래프를 수행한다."""
    prompts, _ = resolve_execution_prompt_bundle(
        req,
        prompt_fragments,
        build_prompt_bundle,
        {
            "investigatorSystemPrompt": "title-suggestion.investigator.system",
            "repairDirective": "title-suggestion.investigator.repair",
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
    reader = TitleLedgerReader(tracer)
    budget = ExecutionBudget(req.limits.budgetUsd, ModelRates(req.modelRates))
    context = ValidationGraphContext(
        AGENT_NAME,
        usage,
        node_registry(
            [
                InvestigateNode(
                    req,
                    reader,
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
    final = await TITLE_SUGGESTION_GRAPH.ainvoke(
        {
            "task_id": req.taskId,
            "language": req.language,
            "context": req.context,
            "messages": [],
            "model_cost_usd": 0.0,
            "candidate": None,
            "validation_errors": [],
            "repair_attempted": False,
            "result": None,
        },
        context=context,
        config=recursion_config(
            20,
            TraceSafeMetadata(
                agent_name=AGENT_NAME,
                model_requested=req.model,
                prompt_version=PROMPT_VERSION,
                job_id=req.jobId,
            ),
        ),
    )
    return final["result"] or {"suggestions": []}
