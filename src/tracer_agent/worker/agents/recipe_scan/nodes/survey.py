"""recipe-scan의 조사 계획 노드를 제공한다."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from tracer_agent.shared.agents.recipe_scan.models import (
    DispatchPlan,
    RecipeScanRequest,
    RecipeScanState,
    SurveyUpdate,
)

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.llm.budget import AgentBudgetLease, ExecutionBudget
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
        lease: AgentBudgetLease,
        system_prompt: str,
    ) -> None:
        self._req = req
        self._usage = usage
        self._chat = chat
        self._fallback_chat = fallback_chat
        self._budget = budget
        self._lease = lease
        self._system_prompt = system_prompt

    async def run(self, _state: RecipeScanState) -> SurveyUpdate:
        req = self._req
        lease = self._lease
        # 예약에서 턴을 하나도 못 받으면 재귀 한도가 0이 되어 그래프가 시작하지 못하므로 모델을 부르지 않는다.
        if lease.max_turns <= 0:
            return {"plan": DispatchPlan(probes=[]), "model_cost_usd": 0.0}
        budget = self._budget.new_loop(f"{AGENT_NAME}:survey", req.model, max_cost_usd=lease.max_cost_usd)
        agent = build_recipe_agent(
            self._chat,
            self._system_prompt,
            [],
            (),
            output=DispatchPlan,
            fallback_chat=self._fallback_chat,
            max_turns=lease.max_turns,
        )
        result = await invoke_structured_agent(
            agent,
            messages=[HumanMessage(content=build_survey_prompt(req.taskId, req.userPrompt))],
            context=StandardAgentContext(
                agent_name=f"{AGENT_NAME}:survey",
                trace=self._usage,
                budget=budget,
                max_model_turns=lease.max_turns,
            ),
            response_type=DispatchPlan,
            recursion_limit=recursion_limit_for(lease.max_turns),
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
