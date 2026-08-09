"""수리를 다시 받는 횟수가 계약이 적은 수와 같은지 검증한다."""

from __future__ import annotations

import json

import pytest

from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.routing import (
    SUPPORTED_REPAIR_ATTEMPTS,
    ValidationReasons,
    build_validation_router,
)
from tracer_agent.worker.agents.shared.execution_reservation import repair_attempts

REASONS = ValidationReasons(passed="통과", repairable="수리", exhausted="소진")


def test_계약이_적은_수를_그대로_읽는다() -> None:
    declared = json.loads(
        (CONTRACT_ROOT / "agent" / "shared" / "execution.budget.json").read_text(encoding="utf-8")
    )

    assert repair_attempts() == declared["reservation"]["repair"]["attempts"]


def test_계약이_이_기계가_실행할_수_없는_수를_적으면_분기를_세우지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracer_agent.worker.agents.runtime.routing.repair_attempts",
        lambda: SUPPORTED_REPAIR_ATTEMPTS + 1,
    )

    with pytest.raises(ValueError, match="counting router"):
        build_validation_router(ExecutionTrace(), "validate", REASONS)
