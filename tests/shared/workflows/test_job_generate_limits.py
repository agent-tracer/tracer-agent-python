"""잡 종류마다 실을 생성 상한이 계약이 적은 규칙에서 나오는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import workflow_contract
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind
from tracer_agent.shared.workflows.jobs_spec import generate_limits

_JOBS = workflow_contract("queues.yaml")["jobWorkflows"]
_PER_KIND = _JOBS["perKind"]

# 계약이 잡 종류마다 워크플로를 두는 축의 이름으로 적으므로 이 축의 종류와 그 이름을 잇는다.
_CONTRACT_KEY = {
    AgentJobKind.TITLE_SUGGESTION: "titleSuggestion",
    AgentJobKind.RECIPE_SCAN: "recipeScan",
    AgentJobKind.TASK_CLEANUP: "taskCleanup",
}


def _declared_generate(key: str) -> dict[str, int] | None:
    """계약이 그 종류의 생성 활동에 적은 상한을 내며 적지 않았으면 아무것도 내지 않는다."""
    for activity in _PER_KIND[key].get("activities") or ():
        if activity["name"].startswith("generate") and "startToCloseSeconds" in activity:
            return activity
    return None


def test_계약이_세_종류를_모두_적는다() -> None:
    # 한 종류라도 비면 이 축이 그 상한을 스스로 골라야 하므로 그 사실을 여기서 드러낸다.
    declared = {key for key in _CONTRACT_KEY.values() if _declared_generate(key) is not None}

    assert declared == set(_CONTRACT_KEY.values())


@pytest.mark.parametrize("kind", list(AgentJobKind), ids=lambda kind: kind.wire)
def test_계약이_상한을_적은_종류는_그_값을_싣는다(kind: AgentJobKind) -> None:
    declared = _declared_generate(_CONTRACT_KEY[kind])
    if declared is None:
        pytest.skip("계약이 이 종류의 상한을 적지 않는다")
    limits = generate_limits(kind)

    assert limits.start_to_close_s == declared["startToCloseSeconds"]
    assert limits.schedule_to_close_s == declared["scheduleToCloseSeconds"]
    assert limits.heartbeat_s == declared["heartbeatTimeoutSeconds"]
    assert limits.max_attempts == declared["maximumAttempts"]
