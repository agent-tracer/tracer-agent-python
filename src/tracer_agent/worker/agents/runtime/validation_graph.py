"""검증과 한 번의 수리로 끝나는 그래프 꼬리와 노드 관측을 제공한다."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from langgraph.graph import END, StateGraph
from langgraph.runtime import Runtime

from .execution.trace import ExecutionTrace

type ValidationNode = Callable[[Any], Awaitable[dict[str, Any]]]
type ValidationRouteName = Literal["repair", "finalize", "empty"]
type ValidationRoute = Callable[[Any], ValidationRouteName]

REPAIR: ValidationRouteName = "repair"
FINALIZE: ValidationRouteName = "finalize"
EMPTY: ValidationRouteName = "empty"


@dataclass(frozen=True)
class ValidationGraphContext:
    """정적 검증 그래프에 요청별 노드와 관측 의존성을 주입한다."""

    agent_name: str
    trace: ExecutionTrace
    nodes: dict[str, ValidationNode]
    route_validation: ValidationRoute


def new_graph(state_schema: type[Any]) -> StateGraph[Any, Any, Any, Any]:
    """요청별 의존성을 Runtime Context로 받는 빈 그래프를 연다."""
    # noinspection PyTypeChecker
    return StateGraph(state_schema, context_schema=ValidationGraphContext)


def observed(graph: StateGraph[Any, Any, Any, Any], node_name: str) -> None:
    """노드를 그래프에 올리며 진입·완료·실패를 실행 궤적에 남긴다."""
    graph.add_node(node_name, cast(Any, _dispatch(node_name)))


def add_validation_tail(graph: StateGraph[Any, Any, Any, Any], validation_node: str) -> None:
    """검증에서 수리 한 번을 거쳐 확정이나 빈 결과로 끝나는 공통 꼬리를 붙인다."""
    for node_name in (validation_node, REPAIR, FINALIZE, EMPTY):
        observed(graph, node_name)
    graph.add_conditional_edges(
        validation_node,
        cast(Any, _route),
        {REPAIR: REPAIR, FINALIZE: FINALIZE, EMPTY: EMPTY},
    )
    graph.add_edge(REPAIR, validation_node)
    graph.add_edge(FINALIZE, END)
    graph.add_edge(EMPTY, END)


# noinspection PyTypeChecker
def _dispatch(node_name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def run(state: Any, runtime: Runtime[ValidationGraphContext]) -> dict[str, Any]:
        context = runtime.context
        trace = context.trace
        trace.record_graph_event(
            "node.started", f"{context.agent_name} entered {node_name}", node_name=node_name
        )
        started = time.monotonic()
        try:
            result = await context.nodes[node_name](state)
        except BaseException:
            trace.record_graph_event(
                "node.failed",
                f"{context.agent_name} failed in {node_name}",
                node_name=node_name,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        trace.record_graph_event(
            "node.completed",
            f"{context.agent_name} completed {node_name}",
            node_name=node_name,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    return run


# noinspection PyTypeChecker
def _route(state: Any, runtime: Runtime[ValidationGraphContext]) -> ValidationRouteName:
    return runtime.context.route_validation(state)
