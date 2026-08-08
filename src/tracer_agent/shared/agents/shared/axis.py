"""이 서비스를 실행한 축의 이름을 두 구현체가 함께 읽는 계약에서 가져온다."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

import yaml

from .contract_root import CONTRACT_ROOT

AGENT_API_SPEC_PATH = CONTRACT_ROOT / "http" / "agent-api.openapi.yaml"
METRICS_PATH = CONTRACT_ROOT / "workflow" / "metrics.yaml"

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


@lru_cache(maxsize=1)
def agent_axis() -> AgentAxis:
    """이 서비스를 실행한 축이며 계약이 선언하지 않은 이름이면 거절한다."""
    axis: AgentAxis = "python"
    if axis not in declared_axes():
        raise UndeclaredAgentAxisError(f"AgentAxis does not declare {axis!r}")
    return axis


if TYPE_CHECKING:
    AGENT_BACKEND: AgentAxis
    AXIS_ATTRIBUTE_KEY: str
    AXIS_LABEL_NAME: str

_LAZY: dict[str, Any] = {
    "AGENT_BACKEND": agent_axis,
    "AXIS_ATTRIBUTE_KEY": axis_attribute_key,
    "AXIS_LABEL_NAME": axis_label_name,
}


def __getattr__(name: str) -> Any:
    """계약 조각을 읽는 값은 모듈을 실을 때가 아니라 처음 물을 때 정해진다."""
    read = _LAZY.get(name)
    if read is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return read()
