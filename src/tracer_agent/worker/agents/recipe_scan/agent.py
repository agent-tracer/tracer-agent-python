"""recipe-scan의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from typing import Any, cast

from tracer_agent.shared.agents.recipe_scan.models import (
    ProvenanceCatalog,
    RecipeScanRequest,
    RecipeScanResult,
    RecipeScanState,
    initial_recipe_scan_state,
)
from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.durable_graph import PriorSpend
from ..runtime.execution.trace import ExecutionTrace
from ..runtime.job_agent import GraphRun, JobGraphAgent, dumped
from ..runtime.llm.budget import ExecutionBudget
from ..runtime.llm.client import ChatPair
from ..runtime.node import NodeRegistry
from ..runtime.pricing import ModelRates
from ..runtime.routes import EMPTY, FINALIZE
from ..runtime.routing import build_validation_router
from ..runtime.tracer_client import TracerApiPort
from ..runtime.validation_graph import ValidationGraphContext
from ..runtime.validation_nodes import ResultNode
from ..shared.prompt_source_port import AgentPrompt
from .deps import RecipeDeps, new_recipe_caller
from .graph import PROBE_WALL_CLOCK_CEILING_S, RECIPE_SCAN_GRAPH, RECIPE_SCAN_NODE_NAMES
from .nodes.candidate import InvestigateNode, RepairNode, ValidateCandidateNode
from .nodes.probe import ProbeNode
from .nodes.result import empty_result, finalize_result, wire_provenance
from .nodes.survey import SurveyNode
from .outputs import DEFAULT_LANGUAGE, deliver_recipes
from .policy import VALIDATION_REASONS
from .prompts import build_prompt_bundle
from .reader import RecipeLedgerReader
from .reservation import load_reservation_policy
from .search import RecipeSearchReader


class RecipeScanJob(JobGraphAgent[RecipeScanRequest, RecipeScanState]):
    """전문가를 팬아웃해 재사용 가능한 레시피 후보를 세우는 잡이다."""

    kind = AgentJobKind.RECIPE_SCAN
    topology = RECIPE_SCAN_GRAPH
    # 조사 계획과 전문가 팬아웃과 종합과 수리가 실행되는 동안 그래프가 밟는 노드 수의 상한이다.
    recursion_limit = 30

    async def prepare(self, payload: JsonObject, _tracer: TracerApiPort) -> RecipeScanRequest:
        """접수가 실은 값만으로 스캔 요청을 세운다."""
        return RecipeScanRequest.model_validate(payload)

    async def settle_outputs(
        self,
        tracer: TracerApiPort,
        execution_id: str,
        data: JsonObject | None,
        payload: JsonObject,
    ) -> None:
        """레시피 후보를 창구로 보내며 보낼 것이 없으면 아무 창구도 부르지 않는다."""
        if not data:
            return
        await deliver_recipes(tracer, execution_id, data, _language_of(payload))

    def compose(
        self,
        req: RecipeScanRequest,
        tracer: TracerApiPort,
        usage: ExecutionTrace,
        prompt: AgentPrompt,
        chats: ChatPair,
        prior: PriorSpend,
    ) -> GraphRun[RecipeScanState]:
        budget = ExecutionBudget(
            req.limits.budgetUsd,
            ModelRates(req.modelRates),
            max_turns=req.limits.maxTurns,
            spent_usd=prior.cost_usd,
            turns_used=prior.turns,
        )
        # 뗄 순서를 바꾸면 그 시점 잔량에 곱하는 비율이 달라지므로 계약이 적은 순서를 지킨다.
        policy = load_reservation_policy()
        repair_lease = budget.reserve(policy.repair.turns, policy.repair.budget_share)
        survey_lease = budget.reserve(policy.survey.turns, policy.survey.budget_share)
        synthesis_floor_lease = budget.reserve(
            policy.synthesis_floor.turns, policy.synthesis_floor.budget_share
        )
        deps = RecipeDeps(
            req=req,
            reader=RecipeLedgerReader(tracer),
            search=RecipeSearchReader(tracer),
            usage=usage,
            caller=new_recipe_caller(chats),
            budget=budget,
            prompts=build_prompt_bundle(prompt),
            prompt=prompt,
            language_directives=prompt.language_directives,
        )
        context: ValidationGraphContext[RecipeScanState] = ValidationGraphContext(
            self.kind,
            usage,
            NodeRegistry(
                {
                    SurveyNode.name: SurveyNode(deps, survey_lease),
                    ProbeNode.name: ProbeNode(deps, PROBE_WALL_CLOCK_CEILING_S),
                    InvestigateNode.name: InvestigateNode(deps, synthesis_floor_lease),
                    ValidateCandidateNode.name: ValidateCandidateNode(usage),
                    RepairNode.name: RepairNode(deps, repair_lease),
                    FINALIZE: ResultNode(FINALIZE, finalize_result(usage)),
                    EMPTY: ResultNode(EMPTY, empty_result(usage)),
                },
                RECIPE_SCAN_NODE_NAMES,
            ),
            build_validation_router(usage, ValidateCandidateNode.name, VALIDATION_REASONS),
        )
        initial = initial_recipe_scan_state(
            req,
            max_cost_usd=budget.remaining_budget_usd,
            max_turns=budget.remaining_turns,
        )
        return GraphRun(initial, context)

    def result_of(self, final: dict[str, Any]) -> JsonObject:
        result: RecipeScanResult = final["result"] or RecipeScanResult(
            provenance=wire_provenance(ProvenanceCatalog())
        )
        # 원장과 조회와 창구 배달이 이 한 벌을 나눠 쓰므로 가림도 이 자리에 선다.
        return cast("JsonObject", redact(dumped(result, exclude_none=True), stage=RedactionStage.OUTPUT))


RECIPE_SCAN_JOB = RecipeScanJob()


def _language_of(payload: JsonObject) -> str:
    """이 실행이 요청받은 답변 언어이며 요청이 정하지 않았으면 정하지 않았다는 표시를 낸다."""
    language = payload.get("language")
    return language if isinstance(language, str) and language else DEFAULT_LANGUAGE
