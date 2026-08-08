"""검증과 한 번의 수리로 끝나는 그래프 꼬리와 노드 관측을 제공한다."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, TimeoutPolicy

from .execution.trace import ExecutionTrace
from .node import NodeRegistry
from .routes import EMPTY, FINALIZE, REPAIR, ValidationRoute, ValidationRouteName

type NodeErrorHandler = Callable[..., Any]


@dataclass(frozen=True)
class ValidationGraphContext[StateT]:
    """정적 검증 그래프에 요청별 노드와 관측 의존성을 주입한다."""

    agent_name: str
    trace: ExecutionTrace
    nodes: NodeRegistry
    route_validation: ValidationRoute[StateT]


# LangGraph 스텁이 runtime 인자를 실은 노드 시그니처를 싣지 못하므로 이 모듈이 좁힘을 소유한다.
def _as_graph_callable(handler: Callable[..., Any]) -> Any:
    return handler


def new_graph(state_schema: type[Any]) -> StateGraph[Any, Any, Any, Any]:
    """요청별 의존성을 Runtime Context로 받는 빈 그래프를 연다."""
    return StateGraph(state_schema, context_schema=ValidationGraphContext)


def declared_node_names(graph: StateGraph[Any, Any, Any, Any]) -> frozenset[str]:
    """위상이 직접 세운 노드의 이름만 낸다."""
    # 오류 처리기를 붙인 노드마다 LangGraph 가 자기 몫의 노드를 더하므로 그 자리는 세지 않는다.
    return frozenset(name for name in graph.nodes if not name.startswith("__"))


def observed(
    graph: StateGraph[Any, Any, Any, Any],
    node_name: str,
    *,
    retry_policy: RetryPolicy | None = None,
    error_handler: NodeErrorHandler | None = None,
    timeout: TimeoutPolicy | None = None,
) -> None:
    """노드를 그래프에 올리며 진입·완료·실패를 실행 궤적에 남긴다."""
    graph.add_node(
        node_name,
        _as_graph_callable(_dispatch(node_name)),
        retry_policy=retry_policy,
        error_handler=_as_graph_callable(error_handler) if error_handler else None,
        timeout=timeout,
    )


def add_validation_tail(graph: StateGraph[Any, Any, Any, Any], validation_node: str) -> None:
    """검증에서 수리 한 번을 거쳐 확정이나 빈 결과로 끝나는 공통 꼬리를 붙인다."""
    for node_name in (validation_node, REPAIR, FINALIZE, EMPTY):
        observed(graph, node_name)
    graph.add_conditional_edges(
        validation_node,
        _as_graph_callable(_route),
        {REPAIR: REPAIR, FINALIZE: FINALIZE, EMPTY: EMPTY},
    )
    graph.add_edge(REPAIR, validation_node)
    graph.add_edge(FINALIZE, END)
    graph.add_edge(EMPTY, END)


def _dispatch(node_name: str) -> Callable[..., Awaitable[Mapping[str, object]]]:
    async def run(state: Any, runtime: Runtime[ValidationGraphContext[Any]]) -> Mapping[str, object]:
        context = runtime.context
        trace = context.trace
        trace.record_orchestration_event(
            "node.started", f"{context.agent_name} entered {node_name}", node_name=node_name
        )
        started = time.monotonic()
        try:
            result = await context.nodes[node_name](state)
        except BaseException:
            trace.record_orchestration_event(
                "node.failed",
                f"{context.agent_name} failed in {node_name}",
                node_name=node_name,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        trace.record_orchestration_event(
            "node.completed",
            f"{context.agent_name} completed {node_name}",
            node_name=node_name,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    return run


def _route(state: Any, runtime: Runtime[ValidationGraphContext[Any]]) -> ValidationRouteName:
    return runtime.context.route_validation(state)
