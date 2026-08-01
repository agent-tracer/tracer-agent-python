"""에이전트 호출 시간 지표가 TypeScript 축과 같은 이름과 속성으로 나오는지 고정한다."""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

METRIC_NAME = "gen_ai.invoke_agent.duration"

# 공급자는 프로세스에서 한 번만 세울 수 있으므로 이 모듈이 하나를 세우고 속성으로 가른다.
_READER = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[_READER]))


def _points_for(agent_name: str) -> list[Any]:
    data = _READER.get_metrics_data()
    if data is None:
        return []
    return [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == METRIC_NAME
        for point in metric.data.data_points
        if dict(point.attributes).get("gen_ai.agent.name") == agent_name
    ]


@pytest.mark.asyncio
async def test_에이전트_호출이_끝나면_실행_시간을_기록한다() -> None:
    from tracer_agent.worker.agents.runtime.telemetry.spans import invoke_agent_span

    async with invoke_agent_span(job_id="job-1", agent_name="title-suggestion", model="claude"):
        pass

    points = _points_for("title-suggestion")
    assert len(points) == 1
    assert points[0].count == 1


@pytest.mark.asyncio
async def test_실패한_호출은_오류_종류를_속성으로_남긴다() -> None:
    from tracer_agent.worker.agents.runtime.telemetry.spans import invoke_agent_span

    with pytest.raises(ValueError):
        async with invoke_agent_span(job_id="job-2", agent_name="task-cleanup", model="claude"):
            raise ValueError("멈춤")

    points = _points_for("task-cleanup")
    assert len(points) == 1
    assert dict(points[0].attributes)["error.type"] == "ValueError"


@pytest.mark.asyncio
async def test_성공한_호출에는_오류_종류를_싣지_않는다() -> None:
    from tracer_agent.worker.agents.runtime.telemetry.spans import invoke_agent_span

    async with invoke_agent_span(job_id="job-3", agent_name="recipe-scan", model="claude"):
        pass

    assert "error.type" not in dict(_points_for("recipe-scan")[0].attributes)
