"""워커가 여는 Temporal SDK 지표 창구를 두 구현체가 함께 읽는 계약에서 세운다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND, AXIS_LABEL_NAME

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
METRICS_PATH = Path(__file__).resolve().parents[3] / "contract" / "workflow" / "metrics.yaml"

_SECONDS = "seconds"


class UnsupportedDurationUnitError(ValueError):
    """계약이 정한 기간 단위를 SDK 가 낼 수 없다."""


@lru_cache(maxsize=1)
def sdk_metrics_telemetry() -> TelemetryConfig:
    """계약이 정한 주소와 단위로 창구를 열고 축의 라벨을 모든 지표에 싣는다."""
    declared: dict[str, Any] = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))["workerSdkMetrics"]
    unit = str(declared["durationUnit"])
    if unit != _SECONDS:
        raise UnsupportedDurationUnitError(f"worker SDK metrics duration unit {unit!r} is not supported")
    return TelemetryConfig(
        metrics=PrometheusConfig(
            bind_address=f"{declared['bindAddress']}:{declared['port']}",
            durations_as_seconds=True,
            counters_total_suffix=bool(declared["countersTotalSuffix"]),
            unit_suffix=bool(declared["unitSuffix"]),
        ),
        global_tags={AXIS_LABEL_NAME: AGENT_BACKEND},
    )


def open_sdk_metrics() -> Runtime:
    """이 프로세스가 만들 클라이언트와 워커가 쓸 runtime 을 지표 창구와 함께 세운다."""
    runtime = Runtime(telemetry=sdk_metrics_telemetry())
    Runtime.set_default(runtime, error_if_already_set=False)
    return runtime
