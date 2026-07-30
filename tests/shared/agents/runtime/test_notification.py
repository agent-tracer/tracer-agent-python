"""잡 상태 알림이 계약의 토픽과 봉투와 어휘를 그대로 따르는지 검증한다."""

from __future__ import annotations

import json

from tests.support.brokers import RecordingProducer
from tests.support.contract import wire_contract
from tracer_agent.shared.agents.runtime.notification import JobStatusNotifier
from tracer_agent.shared.agents.runtime.wakeup import UpdatePublisher
from tracer_agent.shared.workflows.jobs_spec import (
    JOB_UPDATED_NOTIFICATION,
    NOTIFICATIONS_TOPIC,
)


def _declared() -> dict[str, object]:
    topic: dict[str, object] = wire_contract("topics.json")["notifications"]
    return topic


def _notifier(producer: RecordingProducer) -> JobStatusNotifier:
    publisher = UpdatePublisher("broker:9092", NOTIFICATIONS_TOPIC, lambda _brokers: producer)
    return JobStatusNotifier(publisher, JOB_UPDATED_NOTIFICATION)


def test_토픽의_이름은_계약이_선언한_이름과_같다() -> None:
    assert _declared()["name"] == NOTIFICATIONS_TOPIC


def test_잡_갱신_알림의_종류는_계약이_선언한_이름과_같다() -> None:
    types: dict[str, dict[str, str]] = _declared()["types"]  # type: ignore[assignment]
    assert types["jobUpdated"]["name"] == JOB_UPDATED_NOTIFICATION


async def test_발행한_봉투는_계약이_선언한_칸을_갖고_계약이_정한_키로_나뉜다() -> None:
    producer = RecordingProducer()
    declared = _declared()

    assert await _notifier(producer).job_updated(
        "user-1", {"jobId": "job-1", "kind": "recipe.scan", "status": "completed"}
    )

    topic, value, key = producer.sent[0]
    envelope = json.loads(value)
    assert topic == declared["name"]
    assert key.decode() == envelope[declared["key"]]
    assert sorted(envelope) == sorted(declared["payload"])  # type: ignore[arg-type]


async def test_잡_갱신_알림은_계약이_필수로_적은_칸을_싣는다() -> None:
    producer = RecordingProducer()
    types: dict[str, dict[str, dict[str, dict[str, bool]]]] = _declared()["types"]  # type: ignore[assignment]
    required = {name for name, rule in types["jobUpdated"]["payload"].items() if rule["required"]}

    await _notifier(producer).job_updated(
        "user-1", {"jobId": "job-1", "kind": "recipe.scan", "status": "completed"}
    )

    payload = json.loads(producer.sent[0][1])["notification"]["payload"]
    assert required <= set(payload)


async def test_발행이_실패해도_잡을_멈추지_않는다() -> None:
    producer = RecordingProducer(failure=RuntimeError("브로커가 없다"))

    assert await _notifier(producer).job_updated("user-1", {"jobId": "job-1"}) is False
