"""chat의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.graph import END, START
from langgraph.types import RetryPolicy, TimeoutPolicy

from tracer_agent.shared.agents.chat.models import ChatState
from tracer_agent.shared.agents.envelope.catalog import CATALOG
from tracer_agent.shared.agents.shared.model_tiering import CHAT_KIND

from ..runtime.timeouts import deadline_fraction_s
from ..runtime.validation_graph import new_graph, observed
from .nodes.converse import ConverseNode
from .nodes.load_context import CONTEXT_TRANSIENT_ERRORS, LoadContextNode
from .nodes.settle import SettleNode

_CHAT_DEADLINE_MS = CATALOG[CHAT_KIND].deadline_ms

# 재생 문맥은 다시 물어 얻을 수 있으므로 연결 계열 오류만 다시 시도한다.
_CONTEXT_RETRY = RetryPolicy(max_attempts=3, retry_on=CONTEXT_TRANSIENT_ERRORS)
# 도구 루프는 실행 데드라인의 대부분을 쓰되 종결이 실행될 자리를 남긴다.
_CONVERSE_TIMEOUT = TimeoutPolicy(run_timeout=deadline_fraction_s(_CHAT_DEADLINE_MS, 0.9))

_graph = new_graph(ChatState)
observed(_graph, LoadContextNode.name, retry_policy=_CONTEXT_RETRY)
observed(_graph, ConverseNode.name, timeout=_CONVERSE_TIMEOUT)
observed(_graph, SettleNode.name)
_graph.add_edge(START, LoadContextNode.name)
_graph.add_edge(LoadContextNode.name, ConverseNode.name)
_graph.add_edge(ConverseNode.name, SettleNode.name)
_graph.add_edge(SettleNode.name, END)

# 오류 처리기를 붙인 노드마다 LangGraph 가 자기 몫의 노드를 더하므로 그 자리는 세지 않는다.
CHAT_NODE_NAMES: frozenset[str] = frozenset(name for name in _graph.nodes if not name.startswith("__"))

CHAT_GRAPH = _graph.compile()
