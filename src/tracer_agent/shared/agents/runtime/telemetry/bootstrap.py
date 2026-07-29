"""OTLP 추적·메트릭 공급자의 프로세스 생명주기를 구성한다."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import Any


def configure_observability() -> Callable[[], None]:
    """환경 설정에 따라 OTLP 공급자를 구성하고 종료 함수를 돌려준다."""
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint or os.getenv("OTEL_SDK_DISABLED") == "true":
        return _noop

    try:
        resource_mod = importlib.import_module("opentelemetry.sdk.resources")
        trace_api = importlib.import_module("opentelemetry.trace")
        trace_sdk = importlib.import_module("opentelemetry.sdk.trace")
        trace_export = importlib.import_module("opentelemetry.sdk.trace.export")
        otlp_trace = importlib.import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        metrics_api = importlib.import_module("opentelemetry.metrics")
        metrics_sdk = importlib.import_module("opentelemetry.sdk.metrics")
        metrics_export = importlib.import_module("opentelemetry.sdk.metrics.export")
        otlp_metric = importlib.import_module("opentelemetry.exporter.otlp.proto.http.metric_exporter")
    except ModuleNotFoundError:
        return _noop

    service_name = os.getenv("OTEL_SERVICE_NAME", "agents")
    resource = resource_mod.Resource.create({"service.name": service_name})
    tracer_provider = trace_sdk.TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        trace_export.BatchSpanProcessor(otlp_trace.OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
    )
    trace_api.set_tracer_provider(tracer_provider)

    meter_provider = metrics_sdk.MeterProvider(
        resource=resource,
        metric_readers=[
            metrics_export.PeriodicExportingMetricReader(
                otlp_metric.OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics")
            )
        ],
    )
    metrics_api.set_meter_provider(meter_provider)
    _instrument_process_metrics(meter_provider)

    def shutdown() -> None:
        tracer_provider.shutdown()
        meter_provider.shutdown()

    return shutdown


def _instrument_process_metrics(meter_provider: Any) -> None:
    try:
        system_metrics = importlib.import_module("opentelemetry.instrumentation.system_metrics")
    except ModuleNotFoundError:
        return
    system_metrics.SystemMetricsInstrumentor().instrument(meter_provider=meter_provider)


def _noop() -> None:
    return
