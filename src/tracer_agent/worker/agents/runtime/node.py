"""그래프 노드가 자기 이름과 실행을 소유하는 계약과 노드 사전 조립을 제공한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ClassVar

type ValidationNode = Callable[[Any], Awaitable[Mapping[str, object]]]


class GraphNode[InputT, UpdateT: Mapping[str, object]](ABC):
    """그래프 위상과 관측이 쓰는 노드 이름과 노드의 실행을 한 객체에 모은다."""

    name: ClassVar[str]

    @abstractmethod
    async def run(self, state: InputT) -> UpdateT:
        """자기 상태 부분집합이나 분기 페이로드로 실행해 자기 갱신 타입을 낸다."""


class NodeRegistry:
    """그래프가 요구하는 이름과 등록된 노드가 맞는지 조립 시점에 대조한다."""

    def __init__(
        self, nodes: Mapping[str, GraphNode[Any, Mapping[str, object]]], expected: frozenset[str]
    ) -> None:
        names = set(nodes)
        missing = expected - names
        if missing:
            raise ValueError(f"graph nodes are not registered: {sorted(missing)}")
        extra = names - expected
        if extra:
            raise ValueError(f"registered nodes are not on the graph: {sorted(extra)}")
        drifted = sorted(name for name, node in nodes.items() if node.name != name)
        if drifted:
            raise ValueError(f"registered nodes disagree with their own name: {drifted}")
        self._nodes: dict[str, ValidationNode] = {name: node.run for name, node in nodes.items()}

    def __getitem__(self, name: str) -> ValidationNode:
        """그래프가 진입한 노드의 실행을 낸다."""
        return self._nodes[name]
