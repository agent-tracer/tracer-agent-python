"""task-cleanup의 외부 결과를 만드는 종단 그래프 노드를 제공한다."""

from __future__ import annotations

from tracer_agent.shared.agents.task_cleanup.models import ResultUpdate, TaskCleanupState

from ...runtime.node import GraphNode
from ...runtime.validation_graph import EMPTY, FINALIZE


class FinalizeNode(GraphNode):
    """검증된 제안을 보관 작업 결과로 직렬화한다."""

    name = FINALIZE

    async def run(self, state: TaskCleanupState) -> ResultUpdate:
        suggestions = [item.model_dump() for item in state["suggestions"][: state["max_suggestions"]]]
        return {"result": {"suggestions": suggestions}}


class EmptyNode(GraphNode):
    """제안이 없는 정리 작업 결과를 반환한다."""

    name = EMPTY

    async def run(self, _state: TaskCleanupState) -> ResultUpdate:
        return {"result": {"suggestions": []}}
