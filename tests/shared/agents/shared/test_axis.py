"""이 서비스가 쓰는 축의 이름과 라벨이 계약에서 오는지 검증한다."""

from __future__ import annotations

import re
from typing import get_args

from tests.support.contract import workflow_contract
from tracer_agent.shared.agents.shared.axis import (
    AGENT_BACKEND,
    AXIS_ATTRIBUTE_KEY,
    AXIS_LABEL_NAME,
    AgentAxis,
    declared_axes,
)
from tracer_agent.shared.agents.shared.models import AgentRunObservationDTO


class Test축의_이름:
    def test_계약이_선언한_축_안에_있다(self) -> None:
        assert AGENT_BACKEND in declared_axes()

    def test_이_서비스가_다른_축의_이름을_받지_않는다(self) -> None:
        assert get_args(AgentAxis) == (AGENT_BACKEND,)

    def test_관측_원장이_같은_이름을_싣는다(self) -> None:
        observation = AgentRunObservationDTO.model_construct()
        assert observation.backend == AGENT_BACKEND


class Test축의_라벨:
    def test_계측이_계약이_정한_속성_이름을_쓴다(self) -> None:
        declared = workflow_contract("metrics.yaml")["labels"]["axis"]
        assert declared["attributeKey"] == AXIS_ATTRIBUTE_KEY

    def test_지표_창구가_계약이_정한_라벨_이름을_쓴다(self) -> None:
        declared = workflow_contract("metrics.yaml")["labels"]["axis"]
        assert declared["labelName"] == AXIS_LABEL_NAME

    def test_창구가_싣는_이름을_Prometheus_가_그대로_읽는다(self) -> None:
        assert re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", AXIS_LABEL_NAME) is not None
