"""Python 에이전트 테스트의 프로세스 환경과 공유 계측 자원을 고정한다."""

from __future__ import annotations

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# OTel Python은 프로세스당 TracerProvider 등록을 한 번만 허용하므로 수집 전에 여기서 등록해 모든 테스트가 exporter를 공유한다.
SHARED_SPAN_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(SHARED_SPAN_EXPORTER))
trace_api.set_tracer_provider(_PROVIDER)


@pytest.fixture(autouse=True)
def isolate_otel_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """외부 OTLP 설정이 테스트의 전역 provider 선택에 섞이지 않게 한다."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
