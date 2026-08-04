"""검증 결과를 수리·확정·빈 결과 경로로 구분하는 닫힌 분기 기계를 제공한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .execution.trace import ExecutionTrace
from .routes import EMPTY, FINALIZE, REPAIR, ValidatedState, ValidationRoute, ValidationRouteName


@dataclass(frozen=True)
class ValidationReasons:
    """분기 하나가 궤적에 남기는 사유이며 에이전트마다 자기 어휘로 적는다."""

    passed: str
    repairable: str
    exhausted: str


def build_validation_router[StateT: ValidatedState](
    trace: ExecutionTrace,
    validation_node: str,
    reasons: ValidationReasons,
    *,
    has_result: Callable[[StateT], bool] | None = None,
) -> ValidationRoute[StateT]:
    """검증 통과·수리 전·수리 후 소진 세 경우를 정해진 사유 문구와 함께 경로로 구분한다."""

    def route_validation(state: StateT) -> ValidationRouteName:
        if not state["validation_errors"]:
            route = _with_result(has_result, state, FINALIZE)
            reason = reasons.passed
        elif not state["repair_attempted"]:
            route = REPAIR
            reason = reasons.repairable
        else:
            route = _with_result(has_result, state, EMPTY)
            reason = reasons.exhausted
        trace.record_orchestration_event(
            "route.selected",
            f"{validation_node} -> {route}: {reason}",
            node_name=validation_node,
        )
        return route

    return route_validation


def _with_result[StateT: ValidatedState](
    has_result: Callable[[StateT], bool] | None,
    state: StateT,
    default: ValidationRouteName,
) -> ValidationRouteName:
    if has_result is None:
        return default
    return FINALIZE if has_result(state) else EMPTY
