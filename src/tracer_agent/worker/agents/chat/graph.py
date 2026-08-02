"""chat의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.graph import END, START

from tracer_agent.shared.agents.chat.models import ChatState

from ..runtime.validation_graph import new_graph, observed
from .nodes.converse import ConverseNode

_graph = new_graph(ChatState)
observed(_graph, ConverseNode.name)
_graph.add_edge(START, ConverseNode.name)
_graph.add_edge(ConverseNode.name, END)

# 오류 처리기를 붙인 노드마다 LangGraph 가 자기 몫의 노드를 더하므로 그 자리는 세지 않는다.
CHAT_NODE_NAMES: frozenset[str] = frozenset(name for name in _graph.nodes if not name.startswith("__"))

CHAT_GRAPH = _graph.compile()
