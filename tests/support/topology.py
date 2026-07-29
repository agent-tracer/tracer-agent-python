"""컴파일된 그래프의 위상을 사람이 읽는 형태로 뽑는 테스트 장비."""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph


def edge_lines(graph: CompiledStateGraph[Any, Any, Any, Any]) -> set[str]:
    """컴파일된 그래프의 간선을 고정(→)과 조건부(⇢)를 구분한 화살표 줄로 만든다."""
    drawable = graph.get_graph()
    return {f"{edge.source} {'⇢' if edge.conditional else '→'} {edge.target}" for edge in drawable.edges}
