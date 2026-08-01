"""이 서비스를 태운 축의 이름을 두 구현체가 함께 읽는 계약에서 가져온다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
_CONTRACT_ROOT = Path(__file__).resolve().parents[5] / "contract"
AGENT_API_SPEC_PATH = _CONTRACT_ROOT / "http" / "agent-api.openapi.yaml"
METRICS_PATH = _CONTRACT_ROOT / "workflow" / "metrics.yaml"

AgentAxis = Literal["python"]


class UndeclaredAgentAxisError(ValueError):
    """계약의 AgentAxis 가 이 서비스의 축 이름을 갖지 않는다."""


@lru_cache(maxsize=1)
def declared_axes() -> frozenset[str]:
    """계약이 선언한 축 이름 전부를 낸다."""
    document: dict[str, Any] = yaml.safe_load(AGENT_API_SPEC_PATH.read_text(encoding="utf-8"))
    declared = document["components"]["schemas"]["AgentAxis"]["enum"]
    return frozenset(str(name) for name in declared)


@lru_cache(maxsize=1)
def _axis_label() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))
    label: dict[str, Any] = document["labels"]["axis"]
    return label


def axis_attribute_key() -> str:
    """OTLP 로 내보내는 계측이 축을 싣는 속성의 이름이며 수집기가 점을 밑줄로 바꾼다."""
    return str(_axis_label()["attributeKey"])


def axis_label_name() -> str:
    """워커가 여는 지표 창구에 직접 싣는 라벨의 이름이며 수집기를 지나지 않는다."""
    return str(_axis_label()["labelName"])


def _grounded(axis: AgentAxis) -> AgentAxis:
    if axis not in declared_axes():
        raise UndeclaredAgentAxisError(f"AgentAxis does not declare {axis!r}")
    return axis


AGENT_AXIS: AgentAxis = _grounded("python")
AXIS_ATTRIBUTE_KEY = axis_attribute_key()
AXIS_LABEL_NAME = axis_label_name()
