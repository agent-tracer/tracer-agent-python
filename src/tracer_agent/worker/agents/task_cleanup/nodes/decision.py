"""task-cleanup의 조사와 검증과 복구 그래프 노드를 만든다."""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from tracer_agent.shared.agents.shared.graph_state import remaining_cost_usd, remaining_turns
from tracer_agent.shared.agents.task_cleanup.models import (
    MAX_REDISPATCH_ROUNDS,
    CleanupDraft,
    CleanupDraftSuggestion,
    InvestigateUpdate,
    RepairUpdate,
    TaskCleanupState,
    TriagePlan,
    ValidateDecisionsUpdate,
)

from ...runtime.errors import BudgetExceeded
from ...runtime.llm.budget import (
    AgentBudgetLease,
    SharedToolLoopBudget,
    combine_leases,
    floor_lease,
    floor_then_pool_spend,
    reserved_spend,
)
from ...runtime.node import GraphNode
from ...runtime.validation_nodes import ValidationNode
from ..deps import AGENT_NAME, CleanupDeps
from ..policy import validate_suggestions
from ..prompts import build_user_prompt
from ..tools import COORDINATOR_TOOL_NAMES


def _plan_redispatch(
    draft: CleanupDraft, redispatch_count: int, remaining_usd: float, remaining_turns: int
) -> TriagePlan | None:
    """후보를 한 번 더 조사할지 정해 계획을 돌려주거나 남은 몫이 없으면 None을 낸다."""
    if not draft.redispatch or redispatch_count >= MAX_REDISPATCH_ROUNDS:
        return None
    if remaining_usd <= 0.0 or remaining_turns <= 0:
        return None
    return TriagePlan(inspect=draft.redispatch)


@dataclass(frozen=True)
class _Decision:
    """조율 호출 하나가 낸 초안과 이어갈 메시지와 그 호출이 쓴 턴과 달러다."""

    draft: CleanupDraft
    messages: list[BaseMessage]
    turns_used: int
    cost_usd: float


class _DecisionAgent[UpdateT: Mapping[str, Any]](GraphNode[TaskCleanupState, UpdateT], ABC):
    def __init__(self, deps: CleanupDeps, lease: AgentBudgetLease) -> None:
        self._deps = deps
        self._lease = lease

    async def _decide(
        self,
        messages: list[BaseMessage],
        state: TaskCleanupState,
        lease: AgentBudgetLease,
        budget: SharedToolLoopBudget,
    ) -> _Decision:
        deps = self._deps
        # 몫이 남지 않으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if lease.max_turns <= 0:
            return _Decision(CleanupDraft(), messages, 0, 0.0)
        call = await deps.invoke(
            budget=budget,
            system_prompt=deps.prompts.investigator_system,
            tool_names=COORDINATOR_TOOL_NAMES,
            output=CleanupDraft,
            messages=messages,
            missing_response=f"{AGENT_NAME} produced no structured output",
            max_turns=lease.max_turns,
            exposed_candidates=state["exposed_candidates"],
            event_ids_by_task=state["event_ids_by_task"],
        )
        return _Decision(call.response, call.messages, call.num_turns, budget.delta)


class InvestigateNode(_DecisionAgent[InvestigateUpdate]):
    """검토 전문가 보고를 모아 정리 제안을 쓰거나 근거가 얇으면 후보를 한 번 더 조사하게 한다."""

    name = "investigate"

    async def run(self, state: TaskCleanupState) -> InvestigateUpdate:
        deps = self._deps
        # 바닥 예약은 실행당 한 번이므로 redispatch 고리로 다시 들어온 결정은 남은 예약만 받는다.
        floor = floor_lease(state, self._lease)
        pool = AgentBudgetLease(max_turns=remaining_turns(state), max_cost_usd=remaining_cost_usd(state))
        lease = combine_leases([floor, pool])
        decided = await self._decide(
            [
                HumanMessage(
                    content=build_user_prompt(
                        state["scanned_at"],
                        state["max_suggestions"],
                        deps.language_directives[state["language"]],
                        state["reports"],
                    )
                )
            ],
            state,
            lease,
            deps.new_loop(max_cost_usd=lease.max_cost_usd),
        )
        draft = decided.draft
        spend = floor_then_pool_spend(floor, cost_usd=decided.cost_usd, turns_used=decided.turns_used)
        update: InvestigateUpdate = {
            "suggestions": draft.suggestions,
            "messages": decided.messages,
            "exposed_candidates": state["exposed_candidates"],
            "event_ids_by_task": state["event_ids_by_task"],
            **spend,
            "redispatch": None,
            "redispatch_count": state["redispatch_count"],
        }
        plan = _plan_redispatch(
            draft,
            state["redispatch_count"],
            pool.max_cost_usd - spend["pool_cost_usd"],
            pool.max_turns - spend["pool_turns_used"],
        )
        if plan is not None:
            update["redispatch"] = plan
            update["redispatch_count"] = state["redispatch_count"] + 1
            chosen = ", ".join(f"{item.taskId}:{item.depth}" for item in plan.assignments)
            deps.usage.record_orchestration_event(
                "route.selected", f"{self.name} -> redispatch {chosen}", node_name=self.name
            )
        return update


class RepairNode(_DecisionAgent[RepairUpdate]):
    """검증에서 걸린 제안을 한 번 더 고쳐 쓴다."""

    name = "repair"

    async def run(self, state: TaskCleanupState) -> RepairUpdate:
        repair_prompt = [
            *state["messages"],
            HumanMessage(
                content=self._deps.prompts.repair_directive.format(
                    errors="\n".join(state["validation_errors"])
                )
            ),
        ]
        deps = self._deps
        budget = deps.new_loop(max_cost_usd=self._lease.max_cost_usd)
        try:
            decided = await self._decide(repair_prompt, state, self._lease, budget)
        except BudgetExceeded:
            # 수리는 이미 마지막 시도이며 끊긴 수리가 앞서 검증을 통과한 제안까지 잃게 하지 않는다.
            deps.usage.record_orchestration_event(
                "node.failed", f"{self.name} exhausted its reserved budget", node_name=self.name
            )
            return self._update(
                state, state["suggestions"], state["messages"], budget.delta, self._lease.max_turns
            )
        draft = decided.draft
        # 수리가 추가 조사를 요청하면 실을 자리가 없으므로 이미 통과한 제안을 빈 초안으로 덮지 않는다.
        suggestions = (
            state["suggestions"] if draft.redispatch and not draft.suggestions else draft.suggestions
        )
        return self._update(state, suggestions, decided.messages, decided.cost_usd, decided.turns_used)

    def _update(
        self,
        state: TaskCleanupState,
        suggestions: list[CleanupDraftSuggestion],
        messages: list[BaseMessage],
        cost_usd: float,
        turns_used: int,
    ) -> RepairUpdate:
        """수리 한 번이 남기는 갱신이며 예약 리스로 실행했으므로 팬아웃 풀을 건드리지 않는다."""
        return {
            "suggestions": suggestions,
            "messages": messages,
            "exposed_candidates": state["exposed_candidates"],
            "event_ids_by_task": state["event_ids_by_task"],
            "repair_attempted": True,
            **reserved_spend(cost_usd=cost_usd, turns_used=turns_used),
        }


class ValidateDecisionsNode(ValidationNode[TaskCleanupState, ValidateDecisionsUpdate]):
    """정리 제안이 도구가 노출한 후보와 이벤트만 인용하는지 판정한다."""

    name = "validate_decisions"

    def validate(self, state: TaskCleanupState) -> tuple[ValidateDecisionsUpdate, list[str]]:
        valid, errors = validate_suggestions(state["suggestions"], state)
        return {"suggestions": valid, "validation_errors": errors}, errors
