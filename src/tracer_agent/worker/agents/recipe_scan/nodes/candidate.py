"""recipe-scan의 조사와 검증과 복구 그래프 노드를 만든다."""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from typing import Any

from langchain_core.language_models import BaseChatModel

from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_REDISPATCH_ROUNDS,
    DispatchPlan,
    InvestigateUpdate,
    ProvenanceCatalog,
    RecipeDraft,
    RecipeScanRequest,
    RecipeScanState,
    RepairUpdate,
    ValidateCandidateUpdate,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import AgentBudgetLease, ExecutionBudget, combine_leases
from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.llm.structured_agent import invoke_structured_agent, recursion_limit_for
from ...runtime.node import GraphNode
from ..langchain_agent import build_recipe_agent
from ..policy import validate_recipe_candidates
from ..prompts import build_user_prompt
from ..reader import RecipeLedgerReader
from ..search import RecipeSearchReader
from ..tools import COORDINATOR_TOOLS, build_recipe_registry


def _plan_redispatch(
    draft: RecipeDraft, redispatch_count: int, remaining_usd: float, remaining_turns: int
) -> DispatchPlan | None:
    """전문가를 한 번 더 부를지 정해 계획을 돌려주거나 남은 몫이 없으면 None을 낸다."""
    if not draft.redispatch or redispatch_count >= MAX_REDISPATCH_ROUNDS:
        return None
    if remaining_usd <= 0.0 or remaining_turns <= 0:
        return None
    return DispatchPlan(probes=draft.redispatch)


class _CandidateAgent(GraphNode, ABC):
    def __init__(
        self,
        req: RecipeScanRequest,
        reader: RecipeLedgerReader,
        search: RecipeSearchReader,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        *,
        agent_name: str,
        system_prompt: str,
        repair_directive: str,
        language_directives: Mapping[str, str],
    ) -> None:
        self._req = req
        self._reader = reader
        self._search = search
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._budget = budget
        self._agent_name = agent_name
        self._system_prompt = system_prompt
        self._repair_directive = repair_directive
        self._language_directives = language_directives

    async def _invoke_agent(
        self, messages: list[Any], state: RecipeScanState, lease: AgentBudgetLease
    ) -> tuple[RecipeDraft, list[Any], ProvenanceCatalog, int, float]:
        catalog = state["provenance"]
        # 몫이 남지 않으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if lease.max_turns <= 0:
            return RecipeDraft(), messages, catalog, 0, 0.0
        budget = self._budget.new_loop(self._agent_name, self._req.model, max_cost_usd=lease.max_cost_usd)
        # 조율자는 전문가가 합친 장부의 인용만 확인하고 근거를 직접 캐지 않는다.
        registry = build_recipe_registry(
            self._reader, self._search, catalog, COORDINATOR_TOOLS, agent_name=self._agent_name
        )
        agent = build_recipe_agent(
            self._chat,
            self._system_prompt,
            registry.langchain_tools(),
            registry.transient_errors(),
            output=RecipeDraft,
            fallback_chat=self._fallback_chat,
            max_turns=lease.max_turns,
        )
        context = StandardAgentContext(
            agent_name=self._agent_name,
            trace=self._usage,
            budget=budget,
            max_model_turns=lease.max_turns,
        )
        result = await invoke_structured_agent(
            agent,
            messages=messages,
            context=context,
            response_type=RecipeDraft,
            recursion_limit=recursion_limit_for(lease.max_turns),
            missing_response=f"{self._agent_name} produced no structured output",
        )
        return result.response, result.messages, catalog, result.num_turns, budget.delta


class InvestigateNode(_CandidateAgent):
    """전문가 보고를 모아 레시피 후보를 쓰거나 근거가 얇으면 전문가를 한 번 더 부른다."""

    name = "investigate"

    def __init__(
        self,
        req: RecipeScanRequest,
        reader: RecipeLedgerReader,
        search: RecipeSearchReader,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        *,
        agent_name: str,
        system_prompt: str,
        repair_directive: str,
        language_directives: Mapping[str, str],
        synthesis_floor_lease: AgentBudgetLease,
    ) -> None:
        super().__init__(
            req,
            reader,
            search,
            usage,
            chat,
            fallback_chat,
            budget,
            agent_name=agent_name,
            system_prompt=system_prompt,
            repair_directive=repair_directive,
            language_directives=language_directives,
        )
        self._synthesis_floor_lease = synthesis_floor_lease

    async def run(self, state: RecipeScanState) -> InvestigateUpdate:
        remaining_turns = max(state["max_turns"] - state.get("model_turns_used", 0), 0)
        remaining_usd = max(state["max_cost_usd"] - state.get("model_cost_usd", 0.0), 0.0)
        lease = combine_leases(
            [
                self._synthesis_floor_lease,
                AgentBudgetLease(max_turns=remaining_turns, max_cost_usd=remaining_usd),
            ]
        )
        draft, messages, catalog, turns_used, cost_used = await self._invoke_agent(
            [
                {
                    "role": "user",
                    "content": build_user_prompt(
                        state["task_id"],
                        state["user_prompt"],
                        self._language_directives[state["language"]],
                        state["plan"],
                        state["reports"],
                    ),
                }
            ],
            state,
            lease,
        )
        # 종합의 턴 소모 중 바닥 예약분은 팬아웃 잔량 풀 밖에서 왔으므로 풀의 소모로 세지 않는다.
        pool_turns_used = max(turns_used - self._synthesis_floor_lease.max_turns, 0)
        update: InvestigateUpdate = {
            "candidates": draft.recipes,
            "messages": messages,
            "provenance": catalog,
            "model_cost_usd": cost_used,
            "model_turns_used": pool_turns_used,
            "redispatch": None,
            "redispatch_count": state["redispatch_count"],
        }
        plan = _plan_redispatch(
            draft,
            state["redispatch_count"],
            remaining_usd - cost_used,
            remaining_turns - pool_turns_used,
        )
        if plan is not None:
            update["redispatch"] = plan
            update["redispatch_count"] = state["redispatch_count"] + 1
            chosen = ", ".join(f"{probe.probe}:{probe.weight}" for probe in plan.probes)
            self._usage.record_orchestration_event(
                "route.selected", f"{self.name} -> redispatch {chosen}", node_name=self.name
            )
        return update


class RepairNode(_CandidateAgent):
    """검증에서 걸린 후보를 한 번 더 고쳐 쓴다."""

    name = "repair"

    def __init__(
        self,
        req: RecipeScanRequest,
        reader: RecipeLedgerReader,
        search: RecipeSearchReader,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        *,
        agent_name: str,
        system_prompt: str,
        repair_directive: str,
        language_directives: Mapping[str, str],
        lease: AgentBudgetLease,
    ) -> None:
        super().__init__(
            req,
            reader,
            search,
            usage,
            chat,
            fallback_chat,
            budget,
            agent_name=agent_name,
            system_prompt=system_prompt,
            repair_directive=repair_directive,
            language_directives=language_directives,
        )
        self._lease = lease

    async def run(self, state: RecipeScanState) -> RepairUpdate:
        if not state["candidates"]:
            return {"repair_attempted": True}
        repair_prompt = [
            *state["messages"],
            {
                "role": "user",
                "content": self._repair_directive.format(errors="\n".join(state["validation_errors"])),
            },
        ]
        draft, messages, catalog, _turns_used, cost_used = await self._invoke_agent(
            repair_prompt, state, self._lease
        )
        return {
            "candidates": draft.recipes,
            "messages": messages,
            "provenance": catalog,
            "repair_attempted": True,
            "model_cost_usd": cost_used,
        }


class ValidateCandidateNode(GraphNode):
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
        return {"validation_errors": errors}
