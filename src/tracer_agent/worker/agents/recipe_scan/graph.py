"""recipe-scan의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.graph import START
from langgraph.types import Send

from tracer_agent.shared.agents.recipe_scan.models import ProbeDispatch, RecipeScanState

from ..runtime.orchestration import allocate_cost_shares
from ..runtime.validation_graph import EMPTY, add_validation_tail, new_graph, observed
from .nodes.candidate import InvestigateNode, ValidateCandidateNode
from .nodes.probe import ProbeNode
from .nodes.survey import SurveyNode


def _dispatch(state: RecipeScanState) -> list[Send]:
    plan = state["plan"]
    # 조율자가 근거를 캐는 도구를 갖지 않으므로 띄울 전문가가 없으면 바로 빈 결과로 끝낸다.
    if plan is None or not plan.probes:
        return [Send(EMPTY, state)]
    remaining = state["max_cost_usd"] - state.get("model_cost_usd", 0.0)
    if remaining <= 0.0:
        return [Send(EMPTY, state)]
    return [
        Send(ProbeNode.name, ProbeDispatch(assignment=assignment, cost_budget=cost_budget))
        for assignment, cost_budget in allocate_cost_shares(plan.probes, ceiling=remaining)
    ]


def _after_investigate(state: RecipeScanState) -> list[Send]:
    plan = state["redispatch"]
    if plan is None:
        return [Send(ValidateCandidateNode.name, state)]
    return [
        Send(ProbeNode.name, ProbeDispatch(assignment=assignment, cost_budget=cost_budget))
        for assignment, cost_budget in allocate_cost_shares(plan.probes, ceiling=state["redispatch_ceiling"])
    ]


_graph = new_graph(RecipeScanState)
observed(_graph, SurveyNode.name)
observed(_graph, ProbeNode.name)
observed(_graph, InvestigateNode.name)
add_validation_tail(_graph, ValidateCandidateNode.name)
_graph.add_edge(START, SurveyNode.name)
_graph.add_conditional_edges(SurveyNode.name, _dispatch, [ProbeNode.name, EMPTY])
_graph.add_edge(ProbeNode.name, InvestigateNode.name)
_graph.add_conditional_edges(
    InvestigateNode.name, _after_investigate, [ProbeNode.name, ValidateCandidateNode.name]
)

RECIPE_SCAN_GRAPH = _graph.compile()
