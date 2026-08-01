"""워커가 여는 SDK 지표 창구가 계약이 정한 주소와 단위와 축 라벨을 쓰는지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.agents.shared.axis import declared_axes
from tracer_agent.worker.sdk_metrics import sdk_metrics_telemetry

_DECLARED = workflow_contract("metrics.yaml")
_WINDOW = _DECLARED["workerSdkMetrics"]
_AXIS_LABEL = _DECLARED["labels"]["axis"]["key"]


class TestSDK_지표_창구:
    def test_계약이_정한_주소와_포트를_연다(self) -> None:
        metrics = sdk_metrics_telemetry().metrics

        assert metrics is not None
        assert metrics.bind_address == f"{_WINDOW['bindAddress']}:{_WINDOW['port']}"

    def test_기간을_계약이_정한_단위로_낸다(self) -> None:
        metrics = sdk_metrics_telemetry().metrics

        assert metrics is not None
        assert metrics.durations_as_seconds is (_WINDOW["durationUnit"] == "seconds")

    def test_모든_지표에_축의_라벨을_싣는다(self) -> None:
        tags = sdk_metrics_telemetry().global_tags

        assert tags is not None
        assert tags[_AXIS_LABEL] in declared_axes()
