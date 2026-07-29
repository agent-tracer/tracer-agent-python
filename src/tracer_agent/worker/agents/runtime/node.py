"""그래프 노드가 자기 이름과 실행을 소유하는 계약과 노드 사전 조립을 제공한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, cast

from .validation_graph import ValidationNode


class GraphNode(ABC):
    """그래프 위상과 관측이 쓰는 노드 이름과 노드의 실행을 한 객체에 모은다."""

    name: ClassVar[str]

    @abstractmethod
    async def run(self, state: Any) -> Any:
        """자기 상태 부분집합이나 분기 페이로드로 실행해 자기 갱신 타입을 낸다."""


def node_registry(nodes: Sequence[GraphNode]) -> dict[str, ValidationNode]:
    """노드들을 이름을 키로 하는 검증 그래프용 노드 사전으로 모은다."""
    return {node.name: cast(ValidationNode, node.run) for node in nodes}
