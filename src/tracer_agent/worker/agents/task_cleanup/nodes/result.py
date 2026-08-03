"""task-cleanup의 외부 결과를 만드는 종단 그래프 노드를 제공한다."""

from __future__ import annotations

from tracer_agent.shared.agents.task_cleanup.models import (
    CleanupResult,
    ResultUpdate,
    TaskCleanupState,
)

from ...runtime.node import GraphNode
from ...runtime.routes import EMPTY, FINALIZE


class FinalizeNode(GraphNode[TaskCleanupState, ResultUpdate]):
    """검증된 제안을 보관 작업 결과로 직렬화한다."""

    name = FINALIZE

    async def run(self, state: TaskCleanupState) -> ResultUpdate:
        return {"result": CleanupResult(suggestions=state["suggestions"][: state["max_suggestions"]])}


class EmptyNode(GraphNode[TaskCleanupState, ResultUpdate]):
    """제안이 없는 정리 작업 결과를 반환한다."""

    name = EMPTY

    async def run(self, _state: TaskCleanupState) -> ResultUpdate:
        return {"result": CleanupResult()}
