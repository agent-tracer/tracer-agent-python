"""잡 종류마다 실을 생성 상한이 계약이 적은 규칙에서 나오는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import workflow_contract
from tracer_agent.shared.workflows.generate_limits import CONTRACT_KEY, _limits_of, generate_limits
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind

_PER_KIND = workflow_contract("queues.yaml")["jobWorkflows"]["perKind"]


def test_계약이_세_종류를_모두_적는다() -> None:
    # 한 종류라도 비면 이 축이 그 상한을 스스로 골라야 하므로 그 사실을 여기서 드러낸다.
    assert set(CONTRACT_KEY.values()) <= set(_PER_KIND)


@pytest.mark.parametrize("kind", list(AgentJobKind), ids=lambda kind: kind.wire)
def test_종류마다_자기_생성_활동을_찾는다(kind: AgentJobKind) -> None:
    limits = generate_limits(kind)

    assert limits.start_to_close_s > 0
    assert limits.schedule_to_close_s >= limits.start_to_close_s
    assert limits.heartbeat_s > 0
    assert limits.max_attempts > 0


def test_생성_활동을_적지_않은_종류는_기본값으로_떨어지지_않는다() -> None:
    # 파생으로 바꾼 뒤에는 못 찾는 갈래가 조용한 기본값이 되는 것이 새 위험이다.
    with pytest.raises(StopIteration):
        _limits_of({"activities": [{"name": "prepareAgentJob", "startToCloseSeconds": 60}]})


def test_세_종류의_상한이_서로_다르다() -> None:
    # 한 값으로 뭉뚱그리면 종류마다 갖는 뜻이 사라지므로 갈라져 있다는 사실을 고정한다.
    windows = {generate_limits(kind).schedule_to_close_s for kind in AgentJobKind}

    assert len(windows) == len(list(AgentJobKind))
