"""recipe-scan 후보를 외부 응답으로 만드는 종단 규칙을 제공한다."""

from __future__ import annotations

from collections.abc import Callable

from tracer_agent.shared.agents.recipe_scan.models import (
    ProvenanceCatalog,
    ProvenanceWire,
    RecipeScanResult,
    RecipeScanState,
    ResultUpdate,
)
from tracer_agent.shared.agents.shared.models import EmptyResultReason

from ...runtime.execution.trace import ExecutionTrace
from ...runtime.routes import EMPTY, FINALIZE
from ...shared.empty_result import (
    DEGRADED,
    INSUFFICIENT_EVIDENCE,
    default_empty_result_reason,
)


def wire_provenance(catalog: ProvenanceCatalog) -> ProvenanceWire:
    """워커가 소유권 밖의 인용까지 같은 기준으로 거를 수 있도록 이 실행의 근거 장부를 싣는다."""
    return ProvenanceWire(
        eventIdsByTask={task_id: sorted(ids) for task_id, ids in catalog.eventIdsByTask.items()},
        turnIdsByTask={task_id: sorted(ids) for task_id, ids in catalog.turnIdsByTask.items()},
        ruleIds=sorted(catalog.ruleIds),
        recipeRevs=dict(catalog.recipeRevs),
    )


def empty_result_reason(state: RecipeScanState) -> EmptyResultReason:
    """후보 없이 끝난 실행이 왜 비었는지를 계약의 어휘로 판정한다."""
    recorded = state["empty_result_reason"]
    if recorded is not None:
        return recorded
    if state["validation_errors"]:
        # 수리를 쓰고도 검증을 통과하지 못한 산출이라 저장할 패턴이 없던 실행과 갈린다.
        return DEGRADED
    if state["failed_probes"]:
        # 보고를 세우지 못하고 죽은 전문가는 조사 단계가 실패한 것이라 근거가 모자란 것과 갈린다.
        return DEGRADED
    if any(report.exhausted for report in state["reports"]):
        return INSUFFICIENT_EVIDENCE
    return default_empty_result_reason()


def finalize_result(trace: ExecutionTrace) -> Callable[[RecipeScanState], ResultUpdate]:
    """검증된 후보 목록을 레시피 결과로 직렬화하는 종단 규칙이다."""

    def build(state: RecipeScanState) -> ResultUpdate:
        if not state["candidates"]:
            _record_empty(trace, state, FINALIZE)
        return {
            "result": RecipeScanResult(
                recipes=state["candidates"], provenance=wire_provenance(state["provenance"])
            )
        }

    return build


def empty_result(trace: ExecutionTrace) -> Callable[[RecipeScanState], ResultUpdate]:
    """후보가 없는 레시피 결과를 만드는 종단 규칙이다."""

    def build(state: RecipeScanState) -> ResultUpdate:
        _record_empty(trace, state, EMPTY)
        return {"result": RecipeScanResult(provenance=wire_provenance(state["provenance"]))}

    return build


def _record_empty(trace: ExecutionTrace, state: RecipeScanState, node_name: str) -> None:
    """빈 결과의 사유를 궤적에 남겨 저장할 패턴이 없던 실행과 실패한 실행을 원장에서 구분한다."""
    trace.record_orchestration_event(
        "route.selected",
        f"{node_name} -> empty result: {empty_result_reason(state)}",
        node_name=node_name,
    )
