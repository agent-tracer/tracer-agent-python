"""recipe-scan의 조사 계획 노드를 제공한다."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from tracer_agent.shared.agents.recipe_scan.models import (
    DispatchPlan,
    RecipeScanRequest,
    RecipeScanState,
    SurveyUpdate,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import ExecutionBudget
from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.llm.structured_agent import invoke_structured_agent, recursion_limit_for
from ...runtime.node import GraphNode
from ..langchain_agent import build_recipe_agent
from ..prompts import build_survey_prompt

AGENT_NAME = "recipe-scan"


class SurveyNode(GraphNode):
    """조율자가 어디를 얼마나 팔지 weight로 스스로 정하게 한다."""

    name = "survey"

    def __init__(
        self,
        req: RecipeScanRequest,
        usage: ExecutionTrace,
        chat: BaseChatModel,
        fallback_chat: BaseChatModel | None,
        budget: ExecutionBudget,
        system_prompt: str,
    ) -> None:
        self._req = req
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._budget = budget
        self._system_prompt = system_prompt

    async def run(self, _state: RecipeScanState) -> SurveyUpdate:
        req = self._req
        budget = self._budget.new_loop(f"{AGENT_NAME}:survey", req.model)
        agent = build_recipe_agent(
            self._chat,
            self._system_prompt,
            [],
            (),
            output=DispatchPlan,
            fallback_chat=self._fallback_chat,
            max_turns=req.limits.maxTurns,
        )
        result = await invoke_structured_agent(
            agent,
            messages=[{"role": "user", "content": build_survey_prompt(req.taskId, req.userPrompt)}],
            context=StandardAgentContext(
                agent_name=f"{AGENT_NAME}:survey",
                trace=self._usage,
                budget=budget,
                max_model_turns=req.limits.maxTurns,
            ),
            response_type=DispatchPlan,
            recursion_limit=recursion_limit_for(req.limits.maxTurns),
            missing_response="survey produced no structured plan",
        )
        plan = result.response
        chosen = ", ".join(f"{probe.probe}:{probe.weight}" for probe in plan.probes) or "no specialists"
        self._usage.record_orchestration_event(
            "route.selected",
            f"{self.name} -> {chosen}",
            node_name=self.name,
        )
        return {"plan": plan, "model_cost_usd": budget.delta}
