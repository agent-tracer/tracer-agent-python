"""recipe-scan의 조사 계획 노드를 제공한다."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from tracer_agent.shared.agents.recipe_scan.models import (
    DispatchPlan,
    RecipeScanRequest,
    RecipeScanState,
    SurveyUpdate,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import ExecutionBudget
from ...runtime.node import GraphNode
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
        budget: ExecutionBudget,
        system_prompt: str,
    ) -> None:
        self._req = req
        self._usage = usage
        self._budget = budget
        self._planner = chat.with_structured_output(DispatchPlan, include_raw=True)
        self._system_prompt = system_prompt

    async def run(self, _state: RecipeScanState) -> SurveyUpdate:
        req = self._req
        response = await self._planner.ainvoke(
            [
                {
                    "role": "system",
                    "content": self._system_prompt,
                },
                {
                    "role": "user",
                    "content": build_survey_prompt(req.taskId, req.userPrompt),
                },
            ]
        )
        raw = response.get("raw") if isinstance(response, dict) else None
        parsed = response.get("parsed") if isinstance(response, dict) else response
        budget = self._budget.new_loop(f"{AGENT_NAME}:survey", req.model)
        if isinstance(raw, AIMessage):
            self._usage.add_message(raw)
            self._usage.record_message(raw)
            budget.charge(raw)
        plan = parsed if isinstance(parsed, DispatchPlan) else DispatchPlan.model_validate(parsed)
        chosen = ", ".join(f"{probe.probe}:{probe.weight}" for probe in plan.probes) or "no specialists"
        self._usage.record_graph_event(
            "route.selected",
            f"{self.name} -> {chosen}",
            node_name=self.name,
        )
        return {"plan": plan, "model_cost_usd": budget.delta}
