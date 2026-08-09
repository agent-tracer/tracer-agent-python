"""task-cleanup의 실행 의존성과 그래프 노드를 조립한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact
from tracer_agent.shared.agents.task_cleanup.models import (
    CleanupResult,
    TaskCleanupRequest,
    TaskCleanupState,
    initial_task_cleanup_state,
)
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

from ..runtime.durable_graph import PriorSpend
from ..runtime.execution.trace import ExecutionTrace
from ..runtime.job_agent import GraphRun, JobGraphAgent, dumped
from ..runtime.llm.budget import ExecutionBudget
from ..runtime.llm.client import ChatPair
from ..runtime.node import NodeRegistry
from ..runtime.outputs import JobOutputTargets
from ..runtime.pricing import ModelRates
from ..runtime.routes import EMPTY, FINALIZE
from ..runtime.routing import build_validation_router
from ..runtime.scoped_event_reader import ScopedEventReader
from ..runtime.tracer_client import TracerApiPort
from ..runtime.validation_graph import ValidationGraphContext
from ..runtime.validation_nodes import ResultNode
from ..shared.execution_reservation import load_reservation_policy
from ..shared.prompt_source_port import AgentPrompt
from .deps import CleanupDeps, new_cleanup_caller
from .graph import TASK_CLEANUP_GRAPH, TASK_CLEANUP_NODE_NAMES
from .nodes.decision import InvestigateNode, RepairNode, ValidateDecisionsNode
from .nodes.inspect import InspectNode, TriageNode
from .nodes.result import empty_result, finalize_result
from .outputs import write_suggestions
from .policy import VALIDATION_REASONS, has_suggestions
from .prompts import build_prompt_bundle
from .reader import load_cleanup_batch


class TaskCleanupJob(JobGraphAgent[TaskCleanupRequest, TaskCleanupState]):
    """정리 후보를 조사해 보관 제안을 세우는 잡이다."""

    kind = AgentJobKind.TASK_CLEANUP
    topology = TASK_CLEANUP_GRAPH
    # 선별과 후보별 조사와 결정과 수리가 실행되는 동안 그래프가 밟는 노드 수의 상한이다.
    recursion_limit = 30

    async def collect_context(self, payload: JsonObject, tracer: TracerApiPort) -> JsonObject:
        """접수가 후보 배치를 싣지 않았으면 이 시점에 스스로 조립해 실행 입력에 싣는다."""
        if "batch" in payload:
            return payload
        now = datetime.now(UTC)
        batch = await load_cleanup_batch(tracer, now)
        return {**payload, "batch": batch.model_dump(mode="json"), "scannedAt": now.isoformat()}

    async def prepare(self, payload: JsonObject, _tracer: TracerApiPort) -> TaskCleanupRequest:
        """문맥과 봉투가 실린 입력으로 이 시도의 요청을 세운다."""
        return TaskCleanupRequest.model_validate(payload)

    async def settle_outputs(
        self,
        targets: JobOutputTargets,
        execution_id: str,
        data: JsonObject | None,
        payload: JsonObject,
    ) -> None:
        """정리 제안을 자기 원장에 적으며 적을 것이 없으면 원장을 열지 않는다."""
        if not data:
            return
        user_id = payload.get("userId")
        if not isinstance(user_id, str) or not user_id:
            return
        await write_suggestions(targets, user_id, execution_id, data)

    def compose(
        self,
        req: TaskCleanupRequest,
        tracer: TracerApiPort,
        usage: ExecutionTrace,
        prompt: AgentPrompt,
        chats: ChatPair,
        prior: PriorSpend,
        _agent_api: TracerApiPort | None = None,
    ) -> GraphRun[TaskCleanupState]:
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
        triage_lease = budget.reserve(policy.survey.turns, policy.survey.budget_share)
        decision_floor_lease = budget.reserve(
            policy.synthesis_floor.turns, policy.synthesis_floor.budget_share
        )
        deps = CleanupDeps(
            req=req,
            reader=ScopedEventReader(tracer),
            usage=usage,
            caller=new_cleanup_caller(chats),
            budget=budget,
            prompts=build_prompt_bundle(prompt),
            prompt=prompt,
            language_directives=prompt.language_directives,
        )
        context: ValidationGraphContext[TaskCleanupState] = ValidationGraphContext(
            self.kind,
            usage,
            NodeRegistry(
                {
                    TriageNode.name: TriageNode(deps, triage_lease),
                    InspectNode.name: InspectNode(deps),
                    InvestigateNode.name: InvestigateNode(deps, decision_floor_lease),
                    ValidateDecisionsNode.name: ValidateDecisionsNode(usage),
                    RepairNode.name: RepairNode(deps, repair_lease),
                    FINALIZE: ResultNode(FINALIZE, finalize_result),
                    EMPTY: ResultNode(EMPTY, empty_result),
                },
                TASK_CLEANUP_NODE_NAMES,
            ),
            build_validation_router(
                usage, ValidateDecisionsNode.name, VALIDATION_REASONS, has_result=has_suggestions
            ),
        )
        initial = initial_task_cleanup_state(
            req,
            max_cost_usd=budget.remaining_budget_usd,
            max_turns=budget.remaining_turns,
        )
        return GraphRun(initial, context)

    def result_of(self, final: dict[str, Any]) -> JsonObject:
        result: CleanupResult = final["result"] or CleanupResult()
        # 원장과 조회와 창구 배달이 이 한 벌을 나눠 쓰므로 가림도 이 자리에 선다.
        return cast("JsonObject", redact(dumped(result), stage=RedactionStage.OUTPUT))


TASK_CLEANUP_JOB = TaskCleanupJob()
