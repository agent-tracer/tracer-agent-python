"""빈 결과의 사유가 무엇을 보고 갈리는지 고정한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any

from tracer_agent.shared.agents.recipe_scan.models import ProbeReport
from tracer_agent.worker.agents.recipe_scan.nodes.result import empty_result_reason
from tracer_agent.worker.agents.shared.empty_result import (
    DEGRADED,
    INSUFFICIENT_EVIDENCE,
    default_empty_result_reason,
)


def _state(*reports: ProbeReport, failed: list[str] | None = None) -> Any:
    return {
        "reports": list(reports),
        "failed_probes": failed or [],
        "validation_errors": [],
        "empty_result_reason": None,
    }


def _report(*, exhausted: bool = False, truncated: bool = False) -> ProbeReport:
    return ProbeReport(probe="timeline", verdict="판정", exhausted=exhausted, truncated=truncated)


def test_예산이_끊긴_조사는_근거_부족으로_적는다() -> None:
    assert empty_result_reason(_state(_report(exhausted=True))) == INSUFFICIENT_EVIDENCE


def test_글자_수_초과는_빈_결과_사유를_바꾸지_않는다() -> None:
    # 잘라 세운 보고는 전문가가 조사를 마친 결과이며 못 본 것이 있다는 뜻이 아니다.
    assert empty_result_reason(_state(_report(truncated=True))) == default_empty_result_reason()


def test_조사가_없으면_저장할_패턴이_없던_실행으로_적는다() -> None:
    assert empty_result_reason(_state()) == default_empty_result_reason()


def test_보고를_세우지_못하고_죽은_전문가는_생성_실패로_적는다() -> None:
    # 조사 단계가 실패한 실행은 근거가 모자랐던 실행이 아니라 계약의 generation-degraded 다.
    state = _state(_report(), failed=["timeline"])

    assert empty_result_reason(state) == DEGRADED
