"""task-cleanup의 조사와 검증과 복구 그래프 노드를 만든다."""

from __future__ import annotations

from abc import ABC
from typing import Any

from langchain_core.language_models import BaseChatModel

from tracer_agent.shared.agents.task_cleanup.models import (
    MAX_REDISPATCH_ROUNDS,
    CleanupDraft,
    InvestigateUpdate,
    RepairUpdate,
    TaskCleanupRequest,
    TaskCleanupState,
    TriagePlan,
    ValidateDecisionsUpdate,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import ExecutionBudget, SharedToolLoopBudget
from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.llm.structured_agent import invoke_structured_agent, recursion_limit_for
from ...runtime.node import GraphNode
from ..langchain_agent import build_cleanup_agent
from ..policy import validate_suggestions
from ..prompts import build_user_prompt
from ..reader import CleanupLedgerReader
from ..tools import COORDINATOR_TOOL_NAMES, build_cleanup_registry


def _plan_redispatch(
    draft: CleanupDraft, redispatch_count: int, remaining: float
) -> tuple[TriagePlan, float] | None:
    """후보를 한 번 더 열어볼지 정하고 남은 예산과 함께 계획을 돌려주거나 없으면 None을 낸다."""
    if not draft.redispatch or redispatch_count >= MAX_REDISPATCH_ROUNDS:
        return None
    if remaining <= 0.0:
        return None
    return TriagePlan(inspect=draft.redispatch), remaining


class _DecisionAgent(GraphNode, ABC):
    def __init__(
        self,
        req: TaskCleanupRequest,
        reader: CleanupLedgerReader,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        *,
        agent_name: str,
        system_prompt: str,
        repair_directive: str,
    ) -> None:
        self._req = req
        self._reader = reader
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._budget = budget
        self._agent_name = agent_name
        self._system_prompt = system_prompt
        self._repair_directive = repair_directive

    async def _invoke_agent(
        self, messages: list[Any], state: TaskCleanupState
    ) -> tuple[CleanupDraft, list[Any], SharedToolLoopBudget]:
        budget = self._budget.new_loop(self._agent_name, self._req.model)
        # 조율자는 후보를 직접 열어보지 않고 검토 전문가의 보고만으로 제안을 쓴다.
        registry = build_cleanup_registry(
            self._reader,
            self._req.batch,
            state["exposed_candidates"],
            state["event_ids_by_task"],
            agent_name=self._agent_name,
        )
        cleanup_agent = build_cleanup_agent(
            self._chat,
            self._system_prompt,
            registry.langchain_tools(COORDINATOR_TOOL_NAMES),
            registry.transient_errors(),
            output=CleanupDraft,
            fallback_chat=self._fallback_chat,
            max_turns=self._req.limits.maxTurns,
        )
        context = StandardAgentContext(
            agent_name=self._agent_name,
            trace=self._usage,
            budget=budget,
            max_model_turns=self._req.limits.maxTurns,
        )
        result = await invoke_structured_agent(
            cleanup_agent,
            messages=messages,
            context=context,
            response_type=CleanupDraft,
            recursion_limit=recursion_limit_for(self._req.limits.maxTurns),
            missing_response=f"{self._agent_name} produced no structured output",
        )
        return result.response, result.messages, budget


class InvestigateNode(_DecisionAgent):
    """검토 전문가 보고를 모아 정리 제안을 쓰거나 근거가 얇으면 후보를 한 번 더 열어보게 한다."""

    name = "investigate"

    async def run(self, state: TaskCleanupState) -> InvestigateUpdate:
        draft, messages, budget = await self._invoke_agent(
            [
                {
                    "role": "user",
                    "content": build_user_prompt(
                        state["scanned_at"],
                        state["max_suggestions"],
                        state["language"],
                        state["reports"],
                    ),
                }
            ],
            state,
        )
        update: InvestigateUpdate = {
            "suggestions": draft.suggestions,
            "messages": messages,
            "exposed_candidates": state["exposed_candidates"],
            "event_ids_by_task": state["event_ids_by_task"],
            "model_cost_usd": budget.delta,
            "redispatch": None,
            "redispatch_ceiling": 0.0,
            "redispatch_count": state["redispatch_count"],
        }
        remaining = self._budget.max_cost_usd - self._budget.spent
        redispatch = _plan_redispatch(draft, state["redispatch_count"], remaining)
        if redispatch is not None:
            plan, ceiling = redispatch
            update["redispatch"] = plan
            update["redispatch_ceiling"] = ceiling
            update["redispatch_count"] = state["redispatch_count"] + 1
            chosen = ", ".join(f"{item.taskId}:{item.weight}" for item in plan.assignments)
            self._usage.record_graph_event(
                "route.selected", f"{self.name} -> redispatch {chosen}", node_name=self.name
            )
        return update


class RepairNode(_DecisionAgent):
    """검증에서 걸린 제안을 한 번 더 고쳐 쓴다."""

    name = "repair"

    async def run(self, state: TaskCleanupState) -> RepairUpdate:
        repair_prompt = [
            *state["messages"],
            {
                "role": "user",
                "content": self._repair_directive.format(errors="\n".join(state["validation_errors"])),
            },
        ]
        draft, messages, budget = await self._invoke_agent(repair_prompt, state)
        return {
            "suggestions": draft.suggestions,
            "messages": messages,
            "exposed_candidates": state["exposed_candidates"],
            "event_ids_by_task": state["event_ids_by_task"],
            "repair_attempted": True,
            "model_cost_usd": budget.delta,
        }


class ValidateDecisionsNode(GraphNode):
    """정리 제안이 도구가 노출한 후보와 이벤트만 인용하는지 판정한다."""

    name = "validate_decisions"

    def __init__(self, usage: ExecutionTrace) -> None:
        self._usage = usage

    async def run(self, state: TaskCleanupState) -> ValidateDecisionsUpdate:
        valid, errors = validate_suggestions(state["suggestions"], state)
        if errors:
            self._usage.record_graph_event(
                "validation.failed",
                "; ".join(errors),
                node_name=self.name,
            )
        return {"suggestions": valid, "validation_errors": errors}
