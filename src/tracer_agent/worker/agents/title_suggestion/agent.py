"""title-suggestion의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from typing import Any, cast

from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact
from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestionDraft,
    TitleSuggestionRequest,
    TitleSuggestionState,
    initial_title_suggestion_state,
)
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
from ..runtime.scoped_event_reader import ScopedEventReader
from ..runtime.tracer_client import TracerApiPort
from ..runtime.validation_graph import ValidationGraphContext
from ..runtime.validation_nodes import ResultNode
from ..shared.execution_reservation import load_reservation_policy
from ..shared.prompt_source_port import AgentPrompt
from .deps import TitleDeps, new_title_caller
from .graph import TITLE_SUGGESTION_GRAPH, TITLE_SUGGESTION_NODE_NAMES
from .nodes.candidate import (
    InvestigateNode,
    RepairNode,
    ValidateCandidateNode,
    empty_result,
    finalize_result,
)
from .policy import VALIDATION_REASONS
from .prompts import build_prompt_bundle
from .reader import load_title_context


class TitleSuggestionJob(JobGraphAgent[TitleSuggestionRequest, TitleSuggestionState]):
    """대화 발췌로 제목 후보를 세우는 잡이다."""

    kind = AgentJobKind.TITLE_SUGGESTION
    topology = TITLE_SUGGESTION_GRAPH
    # 조사 한 번과 수리 한 번이 도구 루프를 실행하는 동안 그래프가 밟는 노드 수의 상한이다.
    recursion_limit = 20

    async def collect_context(self, payload: JsonObject, tracer: TracerApiPort) -> JsonObject:
        """접수가 대화 발췌를 싣지 않았으면 이 시점에 스스로 조립해 실행 입력에 싣는다."""
        if "context" in payload:
            return payload
        context = await load_title_context(tracer, str(payload["taskId"]))
        return {**payload, "context": context.model_dump(mode="json")}

    async def prepare(self, payload: JsonObject, _tracer: TracerApiPort) -> TitleSuggestionRequest:
        """문맥과 봉투가 실린 입력으로 이 시도의 요청을 세운다."""
        return TitleSuggestionRequest.model_validate(payload)

    def compose(
        self,
        req: TitleSuggestionRequest,
        tracer: TracerApiPort,
        usage: ExecutionTrace,
        prompt: AgentPrompt,
        chats: ChatPair,
        prior: PriorSpend,
    ) -> GraphRun[TitleSuggestionState]:
        budget = ExecutionBudget(
            req.limits.budgetUsd,
            ModelRates(req.modelRates),
            max_turns=req.limits.maxTurns,
            spent_usd=prior.cost_usd,
            turns_used=prior.turns,
        )
        # 조사가 총량을 다 쓰면 고칠 몫이 남지 않으므로 리페어 몫을 계약이 적은 대로 먼저 뗀다.
        repair_lease = budget.reserve(
            load_reservation_policy().repair.turns, load_reservation_policy().repair.budget_share
        )
        deps = TitleDeps(
            req=req,
            reader=ScopedEventReader(tracer),
            usage=usage,
            caller=new_title_caller(chats),
            budget=budget,
            prompts=build_prompt_bundle(prompt),
            language_directives=prompt.language_directives,
        )
        context: ValidationGraphContext[TitleSuggestionState] = ValidationGraphContext(
            self.kind,
            usage,
            NodeRegistry(
                {
                    InvestigateNode.name: InvestigateNode(deps),
                    ValidateCandidateNode.name: ValidateCandidateNode(usage),
                    RepairNode.name: RepairNode(deps, repair_lease),
                    FINALIZE: ResultNode(FINALIZE, finalize_result),
                    EMPTY: ResultNode(EMPTY, empty_result),
                },
                TITLE_SUGGESTION_NODE_NAMES,
            ),
            build_validation_router(usage, ValidateCandidateNode.name, VALIDATION_REASONS),
        )
        initial = initial_title_suggestion_state(
            req,
            max_cost_usd=budget.remaining_budget_usd,
            max_turns=budget.remaining_turns,
        )
        return GraphRun(initial, context)

    def result_of(self, final: dict[str, Any]) -> JsonObject:
        result: TitleSuggestionDraft = final["result"] or TitleSuggestionDraft()
        # 원장과 조회가 이 한 벌을 나눠 쓰므로 가림도 이 자리에 선다.
        return cast("JsonObject", redact(dumped(result), stage=RedactionStage.OUTPUT))


TITLE_SUGGESTION_JOB = TitleSuggestionJob()
