"""recipe-scan 후보를 외부 응답으로 만드는 종단 그래프 노드를 제공한다."""

from __future__ import annotations

from tracer_agent.shared.agents.recipe_scan.models import (
    ProvenanceCatalog,
    ProvenanceWire,
    RecipeScanResult,
    RecipeScanState,
    ResultUpdate,
)

from ...runtime.node import GraphNode
from ...runtime.routes import EMPTY, FINALIZE


def wire_provenance(catalog: ProvenanceCatalog) -> ProvenanceWire:
    """워커가 소유권 밖의 인용까지 같은 기준으로 거를 수 있도록 이 실행의 근거 장부를 싣는다."""
    return ProvenanceWire(
        eventIdsByTask={task_id: sorted(ids) for task_id, ids in catalog.eventIdsByTask.items()},
        turnIdsByTask={task_id: sorted(ids) for task_id, ids in catalog.turnIdsByTask.items()},
        ruleIds=sorted(catalog.ruleIds),
        recipeRevs=dict(catalog.recipeRevs),
    )


class FinalizeNode(GraphNode[RecipeScanState, ResultUpdate]):
    """검증된 후보 목록을 레시피 결과로 직렬화한다."""

    name = FINALIZE

    async def run(self, state: RecipeScanState) -> ResultUpdate:
        return {
            "result": RecipeScanResult(
                recipes=state["candidates"], provenance=wire_provenance(state["provenance"])
            )
        }


class EmptyNode(GraphNode[RecipeScanState, ResultUpdate]):
    """후보가 없는 레시피 결과를 반환한다."""

    name = EMPTY

    async def run(self, state: RecipeScanState) -> ResultUpdate:
        return {"result": RecipeScanResult(provenance=wire_provenance(state["provenance"]))}
