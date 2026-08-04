"""task-cleanup의 조사와 검증과 복구 그래프 노드를 만든다."""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from tracer_agent.shared.agents.task_cleanup.models import (
    MAX_REDISPATCH_ROUNDS,
    CleanupDraft,
    InvestigateUpdate,
    RepairUpdate,
    TaskCleanupState,
    TriagePlan,
    ValidateDecisionsUpdate,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.node import GraphNode
from ..deps import AGENT_NAME, CleanupDeps
from ..policy import validate_suggestions
from ..prompts import build_user_prompt
from ..tools import COORDINATOR_TOOL_NAMES


def _plan_redispatch(
    draft: CleanupDraft, redispatch_count: int, remaining: float
) -> tuple[TriagePlan, float] | None:
    """후보를 한 번 더 조사할지 정하고 남은 예산과 함께 계획을 돌려주거나 없으면 None을 낸다."""
    if not draft.redispatch or redispatch_count >= MAX_REDISPATCH_ROUNDS:
        return None
    if remaining <= 0.0:
        return None
    return TriagePlan(inspect=draft.redispatch), remaining


@dataclass(frozen=True)
class _Decision:
    """조율 호출 하나가 낸 초안과 이어갈 메시지와 그 호출이 쓴 턴과 달러다."""

    draft: CleanupDraft
    messages: list[BaseMessage]
    turns_used: int
    cost_usd: float


class _DecisionAgent[UpdateT: Mapping[str, Any]](GraphNode[TaskCleanupState, UpdateT], ABC):
    def __init__(self, deps: CleanupDeps) -> None:
        self._deps = deps

    async def _decide(self, messages: list[BaseMessage], state: TaskCleanupState) -> _Decision:
        deps = self._deps
        budget = deps.new_loop()
        # 조율자는 후보를 직접 조회하지 않고 검토 전문가의 보고만으로 제안을 쓴다.
        call = await deps.invoke(
            budget=budget,
            system_prompt=deps.prompts.investigator_system,
            tool_names=COORDINATOR_TOOL_NAMES,
            output=CleanupDraft,
            messages=messages,
            missing_response=f"{AGENT_NAME} produced no structured output",
            exposed_candidates=state["exposed_candidates"],
            event_ids_by_task=state["event_ids_by_task"],
        )
        return _Decision(call.response, call.messages, call.num_turns, budget.delta)


class InvestigateNode(_DecisionAgent[InvestigateUpdate]):
    """검토 전문가 보고를 모아 정리 제안을 쓰거나 근거가 얇으면 후보를 한 번 더 조사하게 한다."""

    name = "investigate"

    async def run(self, state: TaskCleanupState) -> InvestigateUpdate:
        deps = self._deps
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
        )
        draft = decided.draft
        update: InvestigateUpdate = {
            "suggestions": draft.suggestions,
            "messages": decided.messages,
            "exposed_candidates": state["exposed_candidates"],
            "event_ids_by_task": state["event_ids_by_task"],
            "model_cost_usd": decided.cost_usd,
            "model_turns_used": decided.turns_used,
            "redispatch": None,
            "redispatch_ceiling": 0.0,
            "redispatch_count": state["redispatch_count"],
        }
        remaining = deps.budget.max_cost_usd - deps.budget.spent
        redispatch = _plan_redispatch(draft, state["redispatch_count"], remaining)
        if redispatch is not None:
            plan, ceiling = redispatch
            update["redispatch"] = plan
            update["redispatch_ceiling"] = ceiling
            update["redispatch_count"] = state["redispatch_count"] + 1
            chosen = ", ".join(f"{item.taskId}:{item.weight}" for item in plan.assignments)
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
        decided = await self._decide(repair_prompt, state)
        return {
            "suggestions": decided.draft.suggestions,
            "messages": decided.messages,
            "exposed_candidates": state["exposed_candidates"],
            "event_ids_by_task": state["event_ids_by_task"],
            "repair_attempted": True,
            "model_cost_usd": decided.cost_usd,
            "model_turns_used": decided.turns_used,
        }


class ValidateDecisionsNode(GraphNode[TaskCleanupState, ValidateDecisionsUpdate]):
    """정리 제안이 도구가 노출한 후보와 이벤트만 인용하는지 판정한다."""

    name = "validate_decisions"

    def __init__(self, usage: ExecutionTrace) -> None:
        self._usage = usage

    async def run(self, state: TaskCleanupState) -> ValidateDecisionsUpdate:
        valid, errors = validate_suggestions(state["suggestions"], state)
        if errors:
            self._usage.record_orchestration_event(
                "validation.failed",
                "; ".join(errors),
                node_name=self.name,
            )
        return {"suggestions": valid, "validation_errors": errors}
