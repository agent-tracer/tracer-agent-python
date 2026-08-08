"""OTLP 추적·메트릭 공급자의 프로세스 생명주기를 구성한다."""

from __future__ import annotations

import os
from collections.abc import Callable

from opentelemetry import metrics as metrics_api
from opentelemetry import trace as trace_api
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ...shared.env import env_flag


def configure_observability() -> Callable[[], None]:
    """환경 설정에 따라 OTLP 공급자를 구성하고 종료 함수를 돌려준다."""
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint or env_flag("OTEL_SDK_DISABLED"):
        return _noop

    resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "agents")})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
    )
    trace_api.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics"))
        ],
    )
    metrics_api.set_meter_provider(meter_provider)
    _instrument_process_metrics(meter_provider)

    def shutdown() -> None:
        tracer_provider.shutdown()
        meter_provider.shutdown()

    return shutdown


def _instrument_process_metrics(meter_provider: MeterProvider) -> None:
    """프로세스 지표 계측은 선택 의존이므로 없으면 세우지 않는다."""
    try:
        from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
    except ModuleNotFoundError:
        return
    SystemMetricsInstrumentor().instrument(meter_provider=meter_provider)


def _noop() -> None:
    return
