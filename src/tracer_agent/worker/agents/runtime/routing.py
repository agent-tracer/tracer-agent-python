"""검증 결과를 수리·확정·빈 결과 경로로 가르는 닫힌 분기 기계를 제공한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .execution.trace import ExecutionTrace
from .validation_graph import EMPTY, FINALIZE, REPAIR, ValidationRoute, ValidationRouteName


def build_validation_router(
    trace: ExecutionTrace,
    validation_node: str,
    *,
    pass_reason: str,
    repair_reason: str,
    exhausted_reason: str,
    has_result: Callable[[Any], bool] | None = None,
) -> ValidationRoute:
    """검증 통과·수리 전·수리 후 소진 세 경우를 정해진 사유 문구와 함께 경로로 가른다."""

    def route_validation(state: Any) -> ValidationRouteName:
        if not state["validation_errors"]:
            route = _with_result(has_result, state, FINALIZE)
            reason = pass_reason
        elif not state["repair_attempted"]:
            route = REPAIR
            reason = repair_reason
        else:
            route = _with_result(has_result, state, EMPTY)
            reason = exhausted_reason
        trace.record_graph_event(
            "route.selected",
            f"{validation_node} -> {route}: {reason}",
            node_name=validation_node,
        )
        return route

    return route_validation


def _with_result(
    has_result: Callable[[Any], bool] | None,
    state: Any,
    default: ValidationRouteName,
) -> ValidationRouteName:
    if has_result is None:
        return default
    return FINALIZE if has_result(state) else EMPTY
