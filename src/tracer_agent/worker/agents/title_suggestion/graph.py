"""title-suggestion의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.graph import START

from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionState

from ..runtime.durable_graph import DurableGraph
from ..runtime.validation_graph import add_validation_tail, new_graph, observed
from .nodes.candidate import InvestigateNode, ValidateCandidateNode

_graph = new_graph(TitleSuggestionState)
observed(_graph, InvestigateNode.name)
add_validation_tail(_graph, ValidateCandidateNode.name)
_graph.add_edge(START, InvestigateNode.name)
_graph.add_edge(InvestigateNode.name, ValidateCandidateNode.name)

# 오류 처리기를 붙인 노드마다 LangGraph 가 자기 몫의 노드를 더하므로 그 자리는 세지 않는다.
TITLE_SUGGESTION_NODE_NAMES: frozenset[str] = frozenset(
    name for name in _graph.nodes if not name.startswith("__")
)

TITLE_SUGGESTION_GRAPH = DurableGraph(_graph)
