"""이 서비스가 쓰는 축의 이름과 라벨이 계약에서 오는지 검증한다."""

from __future__ import annotations

from typing import get_args

from tests.support.contract import workflow_contract
from tracer_agent.shared.agents.shared.axis import (
    AGENT_AXIS,
    AXIS_LABEL_KEY,
    AgentAxis,
    declared_axes,
)
from tracer_agent.shared.agents.shared.models import AgentRunObservationDTO


class Test축의_이름:
    def test_계약이_선언한_축_안에_있다(self) -> None:
        assert AGENT_AXIS in declared_axes()

    def test_이_서비스가_다른_축의_이름을_받지_않는다(self) -> None:
        assert get_args(AgentAxis) == (AGENT_AXIS,)

    def test_관측_원장이_같은_이름을_싣는다(self) -> None:
        observation = AgentRunObservationDTO.model_construct()
        assert observation.backend == AGENT_AXIS


class Test축의_라벨:
    def test_계약이_정한_라벨_이름을_쓴다(self) -> None:
        assert workflow_contract("metrics.yaml")["labels"]["axis"]["key"] == AXIS_LABEL_KEY
