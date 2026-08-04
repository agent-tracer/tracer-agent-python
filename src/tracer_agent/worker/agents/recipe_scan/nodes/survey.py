"""recipe-scan의 조사 계획 노드를 제공한다."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from tracer_agent.shared.agents.recipe_scan.models import (
    DispatchPlan,
    ProvenanceCatalog,
    RecipeScanState,
    SurveyUpdate,
)

from ...runtime.llm.budget import AgentBudgetLease
from ...runtime.node import GraphNode
from ..deps import RecipeDeps
from ..prompts import build_survey_prompt
from ..tools import SURVEY_TOOLS


class SurveyNode(GraphNode[RecipeScanState, SurveyUpdate]):
    """조율자가 어디를 얼마나 팔지 depth로 스스로 정하게 한다."""

    name = "survey"

    def __init__(self, deps: RecipeDeps, lease: AgentBudgetLease) -> None:
        self._deps = deps
        self._lease = lease

    async def run(self, _state: RecipeScanState) -> SurveyUpdate:
        deps = self._deps
        lease = self._lease
        # 예약에서 턴을 하나도 못 받으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if lease.max_turns <= 0:
            return {"plan": DispatchPlan(probes=[]), "model_cost_usd": 0.0}
        budget = deps.new_loop(self.name, max_cost_usd=lease.max_cost_usd)
        result = await deps.invoke(
            budget=budget,
            system_prompt=deps.prompts.survey_system,
            # 조율자가 읽은 것은 후보의 인용 근거가 아니므로 장부를 새로 두고 상태에 합치지 않는다.
            catalog=ProvenanceCatalog(),
            tools=SURVEY_TOOLS,
            output=DispatchPlan,
            messages=[
                HumanMessage(
                    content=build_survey_prompt(
                        deps.prompt, deps.req.taskId, deps.req.userPrompt, lease.max_turns
                    )
                )
            ],
            missing_response="survey produced no structured plan",
            max_turns=lease.max_turns,
            call_id=deps.call_id(self.name),
            tool_owner=budget.agent_name,
        )
        plan = result.response
        chosen = ", ".join(f"{probe.probe}:{probe.depth}" for probe in plan.probes) or "no specialists"
        deps.usage.record_orchestration_event(
            "route.selected",
            f"{self.name} -> {chosen}",
            node_name=self.name,
        )
        return {"plan": plan, "model_cost_usd": budget.delta}
