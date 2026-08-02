"""recipe-scan의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.graph import START
from langgraph.types import Send, TimeoutPolicy

from tracer_agent.shared.agents.envelope.catalog import CATALOG
from tracer_agent.shared.agents.recipe_scan.models import ProbeDispatch, RecipeScanState

from ..runtime.orchestration import allocate_cost_shares
from ..runtime.timeouts import deadline_fraction_s
from ..runtime.validation_graph import EMPTY, add_validation_tail, new_graph, observed
from .nodes.candidate import InvestigateNode, ValidateCandidateNode
from .nodes.probe import ProbeNode
from .nodes.survey import SurveyNode

_RECIPE_SCAN_DEADLINE_MS = CATALOG["recipe.scan"].deadline_ms

# 전문가가 진전 없이 머무는 상한이며, 몫이 큰 전문가가 자연히 더 오래 도는 것을 막지 않는다.
PROBE_WALL_CLOCK_CEILING_S = deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.3)
_PROBE_SEND_TIMEOUT = TimeoutPolicy(idle_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.3))
_SURVEY_TIMEOUT = TimeoutPolicy(
    run_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.15),
    idle_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.08),
)
_INVESTIGATE_TIMEOUT = TimeoutPolicy(
    run_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.5),
    idle_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.2),
)


def _dispatch(state: RecipeScanState) -> list[Send]:
    plan = state["plan"]
    # 조율자가 근거를 캐는 도구를 갖지 않으므로 띄울 전문가가 없으면 바로 빈 결과로 끝낸다.
    if plan is None or not plan.probes:
        return [Send(EMPTY, state)]
    remaining = state["max_cost_usd"] - state.get("model_cost_usd", 0.0)
    if remaining <= 0.0:
        return [Send(EMPTY, state)]
    return [
        Send(
            ProbeNode.name,
            ProbeDispatch(assignment=assignment, cost_budget=cost_budget),
            timeout=_PROBE_SEND_TIMEOUT,
        )
        for assignment, cost_budget in allocate_cost_shares(plan.probes, ceiling=remaining)
    ]


def _after_investigate(state: RecipeScanState) -> list[Send]:
    plan = state["redispatch"]
    if plan is None:
        return [Send(ValidateCandidateNode.name, state)]
    return [
        Send(
            ProbeNode.name,
            ProbeDispatch(assignment=assignment, cost_budget=cost_budget),
            timeout=_PROBE_SEND_TIMEOUT,
        )
        for assignment, cost_budget in allocate_cost_shares(plan.probes, ceiling=state["redispatch_ceiling"])
    ]


_graph = new_graph(RecipeScanState)
observed(_graph, SurveyNode.name, timeout=_SURVEY_TIMEOUT)
observed(_graph, ProbeNode.name)
observed(_graph, InvestigateNode.name, timeout=_INVESTIGATE_TIMEOUT)
add_validation_tail(_graph, ValidateCandidateNode.name)
_graph.add_edge(START, SurveyNode.name)
_graph.add_conditional_edges(SurveyNode.name, _dispatch, [ProbeNode.name, EMPTY])
_graph.add_edge(ProbeNode.name, InvestigateNode.name)
_graph.add_conditional_edges(
    InvestigateNode.name, _after_investigate, [ProbeNode.name, ValidateCandidateNode.name]
)

RECIPE_SCAN_GRAPH = _graph.compile()
