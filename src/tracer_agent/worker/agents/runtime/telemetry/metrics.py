"""GenAI 모델·도구 실행 측정값을 OpenTelemetry 메트릭에 기록한다."""

from __future__ import annotations

import importlib
from typing import Any

from tracer_agent.shared.agents.shared.models import UsageDTO

from .attributes import build_client_attributes, token_measurements

_METER_NAME = "agents.ai-jobs"
_METRIC_INSTRUMENTS: dict[str, Any] | None = None


def record_client_metrics(
    model: str,
    duration_seconds: float,
    usage: UsageDTO | None,
    error_subtype: str | None,
) -> None:
    """모델 호출 시간과 토큰 사용량을 기록한다."""
    attrs = build_client_attributes(model, error_subtype)
    instruments = _metric_instruments()
    if instruments is None:
        return
    instruments["client_duration"].record(duration_seconds, attrs)
    for value, token_attrs in token_measurements(usage):
        instruments["token_usage"].record(value, {**attrs, **token_attrs})


def record_invoke_agent_duration(duration_seconds: float, attrs: dict[str, str]) -> None:
    """에이전트 호출 하나가 끝까지 걸린 시간을 기록한다."""
    instruments = _metric_instruments()
    if instruments is None:
        return
    instruments["invoke_agent_duration"].record(duration_seconds, attrs)


def record_tool_duration(duration_seconds: float, attrs: dict[str, str]) -> None:
    """도구 실행 시간을 주어진 속성과 함께 기록한다."""
    instruments = _metric_instruments()
    if instruments is None:
        return
    instruments["tool_duration"].record(duration_seconds, attrs)


def _metric_instruments() -> dict[str, Any] | None:
    global _METRIC_INSTRUMENTS
    if _METRIC_INSTRUMENTS is not None:
        return _METRIC_INSTRUMENTS
    try:
        metrics_mod = importlib.import_module("opentelemetry.metrics")
    except ModuleNotFoundError:
        return None
    meter = metrics_mod.get_meter(_METER_NAME)
    _METRIC_INSTRUMENTS = {
        "token_usage": meter.create_histogram("gen_ai.client.token.usage", unit="{token}"),
        "client_duration": meter.create_histogram("gen_ai.client.operation.duration", unit="s"),
        "invoke_agent_duration": meter.create_histogram("gen_ai.invoke_agent.duration", unit="s"),
        "tool_duration": meter.create_histogram("gen_ai.execute_tool.duration", unit="s"),
    }
    return _METRIC_INSTRUMENTS
