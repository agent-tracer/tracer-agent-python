"""한 실행이 여는 모델 호출들이 같은 조합의 컴파일된 agent를 다시 쓰게 한다."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

type CompiledAgent = CompiledStateGraph[Any, Any, Any, Any]


class CompiledAgentCache:
    """같은 열쇠의 agent를 한 실행 안에서 한 번만 컴파일해 다시 쓴다."""

    def __init__(self) -> None:
        self._agents: dict[Hashable, CompiledAgent] = {}

    def compiled(self, key: Hashable, build: Callable[[], CompiledAgent]) -> CompiledAgent:
        """열쇠에 컴파일된 agent가 없을 때만 만들어 보관하고 그 agent를 낸다."""
        agent = self._agents.get(key)
        if agent is None:
            # build가 await 없이 끝나므로 같은 이벤트 루프의 팬아웃이 조회와 보관 사이에 끼어들지 못한다.
            agent = build()
            self._agents[key] = agent
        return agent

    def size(self) -> int:
        """이 실행이 지금까지 컴파일한 agent 수다."""
        return len(self._agents)
