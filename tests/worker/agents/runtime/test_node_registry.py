"""노드 사전과 그래프 위상의 어긋남을 조립 시점에 거는지 검증한다(그래프 실행 없음)."""

from __future__ import annotations

from typing import Any

import pytest

from tracer_agent.worker.agents.runtime.node import GraphNode, NodeRegistry


class _Alpha(GraphNode[dict[str, Any], dict[str, Any]]):
    name = "alpha"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        return state


class _Beta(GraphNode[dict[str, Any], dict[str, Any]]):
    name = "beta"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        return state


_EXPECTED = frozenset({"alpha", "beta"})


class Test노드레지스트리:
    def test_위상이_요구하는_노드를_모두_등록하면_통과한다(self) -> None:
        registry = NodeRegistry({"alpha": _Alpha(), "beta": _Beta()}, _EXPECTED)

        assert registry["alpha"] is not None

    def test_빠진_노드를_이름과_함께_건다(self) -> None:
        with pytest.raises(ValueError, match="alpha"):
            NodeRegistry({"beta": _Beta()}, _EXPECTED)

    def test_위상에_없는_노드를_건다(self) -> None:
        with pytest.raises(ValueError, match="gamma"):
            NodeRegistry(
                {"alpha": _Alpha(), "beta": _Beta(), "gamma": _Alpha()},
                _EXPECTED,
            )

    def test_자기_이름과_다른_자리에_선_노드를_건다(self) -> None:
        with pytest.raises(ValueError, match="beta"):
            NodeRegistry({"alpha": _Alpha(), "beta": _Alpha()}, _EXPECTED)


class Test실행그래프의노드:
    def test_네_에이전트가_자기_위상의_노드를_빠짐없이_등록한다(self) -> None:
        from tracer_agent.worker.agents.chat.graph import CHAT_NODE_NAMES
        from tracer_agent.worker.agents.recipe_scan.graph import RECIPE_SCAN_NODE_NAMES
        from tracer_agent.worker.agents.task_cleanup.graph import TASK_CLEANUP_NODE_NAMES
        from tracer_agent.worker.agents.title_suggestion.graph import TITLE_SUGGESTION_NODE_NAMES

        for names in (
            CHAT_NODE_NAMES,
            RECIPE_SCAN_NODE_NAMES,
            TASK_CLEANUP_NODE_NAMES,
            TITLE_SUGGESTION_NODE_NAMES,
        ):
            assert names, "위상이 노드를 하나도 갖지 않을 수 없다"
            assert not any(name.startswith("__") for name in names)
