"""HTTP 헤더와 OpenTelemetry 추적 문맥을 상호 변환한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opentelemetry import propagate


def extract_trace_context(headers: Mapping[str, str]) -> Any:
    """전달받은 HTTP 헤더에서 추적 부모 문맥을 읽는다."""
    return propagate.extract(headers)


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """현재 추적 문맥을 주어진 HTTP 헤더에 기록한다."""
    propagate.inject(headers)
    return headers
