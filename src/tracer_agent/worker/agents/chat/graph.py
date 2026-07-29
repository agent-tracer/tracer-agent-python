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

CHAT_GRAPH = _graph.compile()
