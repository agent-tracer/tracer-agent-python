"""갱신 발행이 브로커가 없어도 부른 쪽을 멈추지 않는지 검증한다."""

from __future__ import annotations

from tests.support.brokers import RecordingProducer
from tracer_agent.shared.agents.runtime.wakeup import UpdatePublisher

TOPIC = "chat.execution.updates"


async def test_식별자만_담아_보낸다() -> None:
    producer = RecordingProducer()
    publisher = UpdatePublisher("broker:9092", TOPIC, lambda _brokers: producer)

    assert await publisher.publish("e1", {"executionId": "e1"}) is True

    assert producer.sent == [(TOPIC, b'{"executionId": "e1"}', b"e1")]


async def test_생산자를_한_번만_연다() -> None:
    producer = RecordingProducer()
    publisher = UpdatePublisher("broker:9092", TOPIC, lambda _brokers: producer)

    await publisher.publish("e1", {"executionId": "e1"})
    await publisher.publish("e2", {"executionId": "e2"})

    assert producer.starts == 1


async def test_발행이_실패해도_부른_쪽을_멈추지_않는다() -> None:
    producer = RecordingProducer(failure=RuntimeError("브로커가 없다"))
    publisher = UpdatePublisher("broker:9092", TOPIC, lambda _brokers: producer)

    assert await publisher.publish("e1", {"executionId": "e1"}) is False


async def test_연_적이_있으면_닫는다() -> None:
    producer = RecordingProducer()
    publisher = UpdatePublisher("broker:9092", TOPIC, lambda _brokers: producer)

    await publisher.close()
    assert producer.stops == 0

    await publisher.publish("e1", {"executionId": "e1"})
    await publisher.close()
    assert producer.stops == 1
