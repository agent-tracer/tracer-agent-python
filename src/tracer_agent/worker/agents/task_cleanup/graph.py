"""task-cleanup의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.graph import START
from langgraph.types import Send

from tracer_agent.shared.agents.task_cleanup.models import InspectDispatch, TaskCleanupState

from ..runtime.durable_graph import DurableGraph
from ..runtime.orchestration import allocate_cost_shares
from ..runtime.validation_graph import EMPTY, add_validation_tail, new_graph, observed
from .nodes.decision import InvestigateNode, ValidateDecisionsNode
from .nodes.inspect import InspectNode, TriageNode


def _dispatch(state: TaskCleanupState) -> list[Send]:
    plan = state["plan"]
    # 조율자가 도구를 갖지 않으므로 열어볼 후보가 없으면 바로 빈 결과로 끝낸다.
    if plan is None or not plan.assignments:
        return [Send(EMPTY, state)]
    remaining = state["max_cost_usd"] - state.get("model_cost_usd", 0.0)
    if remaining <= 0.0:
        return [Send(EMPTY, state)]
    return [
        Send(InspectNode.name, InspectDispatch(assignment=assignment, cost_budget=cost_budget))
        for assignment, cost_budget in allocate_cost_shares(
            plan.assignments,
            ceiling=remaining,
        )
    ]


def _after_investigate(state: TaskCleanupState) -> list[Send]:
    plan = state["redispatch"]
    if plan is None:
        return [Send(ValidateDecisionsNode.name, state)]
    return [
        Send(InspectNode.name, InspectDispatch(assignment=assignment, cost_budget=cost_budget))
        for assignment, cost_budget in allocate_cost_shares(
            plan.assignments, ceiling=state["redispatch_ceiling"]
        )
    ]


_graph = new_graph(TaskCleanupState)
observed(_graph, TriageNode.name)
observed(_graph, InspectNode.name)
observed(_graph, InvestigateNode.name)
add_validation_tail(_graph, ValidateDecisionsNode.name)
_graph.add_edge(START, TriageNode.name)
_graph.add_conditional_edges(TriageNode.name, _dispatch, [InspectNode.name, EMPTY])
_graph.add_edge(InspectNode.name, InvestigateNode.name)
_graph.add_conditional_edges(
    InvestigateNode.name, _after_investigate, [InspectNode.name, ValidateDecisionsNode.name]
)

# 오류 처리기를 붙인 노드마다 LangGraph 가 자기 몫의 노드를 더하므로 그 자리는 세지 않는다.
TASK_CLEANUP_NODE_NAMES: frozenset[str] = frozenset(
    name for name in _graph.nodes if not name.startswith("__")
)

TASK_CLEANUP_GRAPH = DurableGraph(_graph)
