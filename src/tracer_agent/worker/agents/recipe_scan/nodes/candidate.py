"""recipe-scan의 조사와 검증과 복구 그래프 노드를 만든다."""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_REDISPATCH_ROUNDS,
    DispatchPlan,
    InvestigateUpdate,
    ProvenanceCatalog,
    RecipeDraft,
    RecipeScanState,
    RepairUpdate,
    ValidateCandidateUpdate,
)

from ...runtime.errors import BudgetExceeded
from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import AgentBudgetLease, combine_leases
from ...runtime.node import GraphNode
from ...runtime.telemetry.execution_metrics import (
    record_redispatch_rounds,
    record_validation_failure,
)
from ..deps import AGENT_NAME, RecipeDeps
from ..policy import validate_recipe_candidates
from ..prompts import build_user_prompt
from ..tools import COORDINATOR_TOOLS


@dataclass(frozen=True)
class Synthesis:
    """종합 한 번이 낸 후보와 그 호출이 쓴 장부와 턴과 비용이다."""

    draft: RecipeDraft
    messages: list[BaseMessage]
    provenance: ProvenanceCatalog
    turns_used: int
    cost_usd: float


def _plan_redispatch(
    draft: RecipeDraft, redispatch_count: int, remaining_usd: float, remaining_turns: int
) -> DispatchPlan | None:
    """전문가를 한 번 더 부를지 정해 계획을 돌려주거나 남은 몫이 없으면 None을 낸다."""
    if not draft.redispatch or redispatch_count >= MAX_REDISPATCH_ROUNDS:
        return None
    if remaining_usd <= 0.0 or remaining_turns <= 0:
        return None
    return DispatchPlan(probes=draft.redispatch)


class _CandidateAgent[UpdateT: Mapping[str, Any]](GraphNode[RecipeScanState, UpdateT], ABC):
    def __init__(self, deps: RecipeDeps, lease: AgentBudgetLease) -> None:
        self._deps = deps
        self._lease = lease

    async def _synthesize(
        self, messages: list[BaseMessage], state: RecipeScanState, lease: AgentBudgetLease
    ) -> Synthesis:
        deps = self._deps
        catalog = state["provenance"]
        # 몫이 남지 않으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if lease.max_turns <= 0:
            return Synthesis(RecipeDraft(), messages, catalog, 0, 0.0)
        budget = deps.new_loop(max_cost_usd=lease.max_cost_usd)
        result = await deps.invoke(
            budget=budget,
            system_prompt=deps.prompts.investigator_system,
            catalog=catalog,
            # 조율자는 전문가가 합친 장부의 인용만 확인하고 근거를 직접 수집하지 않는다.
            tools=COORDINATOR_TOOLS,
            output=RecipeDraft,
            messages=messages,
            missing_response=f"{AGENT_NAME} produced no structured output",
            max_turns=lease.max_turns,
            call_id=deps.call_id(self.name),
        )
        return Synthesis(result.response, result.messages, catalog, result.num_turns, budget.delta)


class InvestigateNode(_CandidateAgent[InvestigateUpdate]):
    """전문가 보고를 모아 레시피 후보를 쓰거나 근거가 얇으면 전문가를 한 번 더 부른다."""

    name = "investigate"

    async def run(self, state: RecipeScanState) -> InvestigateUpdate:
        deps = self._deps
        remaining_turns = max(state["max_turns"] - state.get("model_turns_used", 0), 0)
        remaining_usd = max(state["max_cost_usd"] - state.get("model_cost_usd", 0.0), 0.0)
        lease = combine_leases(
            [
                self._lease,
                AgentBudgetLease(max_turns=remaining_turns, max_cost_usd=remaining_usd),
            ]
        )
        synthesis = await self._synthesize(
            [
                HumanMessage(
                    content=build_user_prompt(
                        deps.prompt,
                        state["task_id"],
                        state["user_prompt"],
                        deps.language_directives[state["language"]],
                        state["plan"],
                        state["reports"],
                        state["provenance"],
                    )
                )
            ],
            state,
            lease,
        )
        # 종합의 턴 소모 중 바닥 예약분은 팬아웃 잔량 풀 밖에서 왔으므로 풀의 소모로 세지 않는다.
        pool_turns_used = max(synthesis.turns_used - self._lease.max_turns, 0)
        update: InvestigateUpdate = {
            "candidates": synthesis.draft.recipes,
            "messages": synthesis.messages,
            "provenance": synthesis.provenance,
            "model_cost_usd": synthesis.cost_usd,
            "model_turns_used": pool_turns_used,
            "redispatch": None,
            "redispatch_count": state["redispatch_count"],
        }
        plan = _plan_redispatch(
            synthesis.draft,
            state["redispatch_count"],
            remaining_usd - synthesis.cost_usd,
            remaining_turns - pool_turns_used,
        )
        if plan is not None:
            update["redispatch"] = plan
            update["redispatch_count"] = state["redispatch_count"] + 1
            record_redispatch_rounds(AGENT_NAME, update["redispatch_count"])
            chosen = ", ".join(f"{probe.probe}:{probe.depth}" for probe in plan.probes)
            deps.usage.record_orchestration_event(
                "route.selected", f"{self.name} -> redispatch {chosen}", node_name=self.name
            )
        return update


class RepairNode(_CandidateAgent[RepairUpdate]):
    """검증에서 걸린 후보를 한 번 더 고쳐 쓴다."""

    name = "repair"

    async def run(self, state: RecipeScanState) -> RepairUpdate:
        if not state["candidates"]:
            return {"repair_attempted": True}
        deps = self._deps
        # 직전 산출을 본문에 실어 맥락 정리가 도구 결과를 비운 뒤에도 무엇을 고치는지 남는다.
        previous = RecipeDraft(recipes=state["candidates"]).model_dump_json()
        repair_prompt = [
            *state["messages"],
            HumanMessage(
                content=deps.prompts.repair_directive.format(
                    previous=previous, errors="\n".join(state["validation_errors"])
                )
            ),
        ]
        try:
            synthesis = await self._synthesize(repair_prompt, state, self._lease)
        except BudgetExceeded:
            # 수리는 이미 마지막 시도이며 끊긴 결과는 근거가 부족해 후보를 내지 못한 것과 같다.
            deps.usage.record_orchestration_event(
                "node.failed", f"{self.name} exhausted its reserved budget", node_name=self.name
            )
            return {"candidates": [], "repair_attempted": True}
        return {
            "candidates": synthesis.draft.recipes,
            "messages": synthesis.messages,
            "provenance": synthesis.provenance,
            "repair_attempted": True,
            "model_cost_usd": synthesis.cost_usd,
        }


class ValidateCandidateNode(GraphNode[RecipeScanState, ValidateCandidateUpdate]):
    """레시피 후보가 전문가 장부의 근거만 인용하는지 판정한다."""

    name = "validate_candidate"

    def __init__(self, usage: ExecutionTrace) -> None:
        self._usage = usage

    async def run(self, state: RecipeScanState) -> ValidateCandidateUpdate:
        errors = validate_recipe_candidates(state["candidates"], state["task_id"], state["provenance"])
        if errors:
            self._usage.record_orchestration_event(
                "validation.failed", "; ".join(errors), node_name=self.name
            )
            record_validation_failure(AGENT_NAME, state["repair_attempted"])
        return {"validation_errors": errors}
