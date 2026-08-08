"""task-cleanup의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.graph import START
from langgraph.types import Send

from tracer_agent.shared.agents.shared.graph_state import remaining_cost_usd, remaining_turns
from tracer_agent.shared.agents.task_cleanup.models import (
    InspectAssignment,
    InspectDispatch,
    TaskCleanupState,
)

from ..runtime.durable_graph import DurableGraph
from ..runtime.llm.budget import lease_shares
from ..runtime.routes import EMPTY
from ..runtime.validation_graph import add_validation_tail, declared_node_names, new_graph, observed
from .nodes.decision import InvestigateNode, ValidateDecisionsNode
from .nodes.inspect import InspectNode, TriageNode


def _fan_out(assignments: list[InspectAssignment], remaining_turns: int, remaining_usd: float) -> list[Send]:
    if not assignments or remaining_turns <= 0 or remaining_usd <= 0.0:
        return []
    leases = lease_shares([item.share for item in assignments], remaining_turns, remaining_usd)
    return [
        Send(
            InspectNode.name,
            InspectDispatch(
                assignment=assignment,
                max_turns=lease.max_turns,
                cost_budget=lease.max_cost_usd,
            ),
        )
        for assignment, lease in zip(assignments, leases, strict=True)
    ]


def _remaining(state: TaskCleanupState) -> tuple[int, float]:
    """예약을 뗀 뒤 팬아웃이 아직 쓰지 않은 턴과 달러다."""
    return remaining_turns(state), remaining_cost_usd(state)


def _dispatch(state: TaskCleanupState) -> list[Send]:
    plan = state["plan"]
    # 조율자가 도구를 갖지 않으므로 조회할 후보가 없으면 바로 빈 결과로 끝낸다.
    if plan is None or not plan.assignments:
        return [Send(EMPTY, state)]
    turns, usd = _remaining(state)
    return _fan_out(plan.assignments, turns, usd) or [Send(EMPTY, state)]


def _after_investigate(state: TaskCleanupState) -> list[Send]:
    plan = state["redispatch"]
    if plan is None:
        return [Send(ValidateDecisionsNode.name, state)]
    turns, usd = _remaining(state)
    return _fan_out(plan.assignments, turns, usd) or [Send(ValidateDecisionsNode.name, state)]


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

TASK_CLEANUP_NODE_NAMES: frozenset[str] = declared_node_names(_graph)

TASK_CLEANUP_GRAPH = DurableGraph(_graph)
