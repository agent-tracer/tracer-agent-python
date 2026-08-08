"""다른 프로세스가 보낸 갱신 신호를 듣는 구독이 어느 지점부터 듣는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest

from tracer_agent.shared.agents.chat.surface import updates as updates_module
from tracer_agent.shared.agents.chat.surface.updates import UpdateSubscriber, kafka_consumer

TOPIC = "chat.execution.updates"


class _FakeConsumer:
    """브로커에 닿지 않고 아무 신호도 내지 않는 소비자 대역이다."""

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def __aiter__(self) -> _FakeConsumer:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


def test_갱신_구독은_지난_신호를_되짚지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    passed: dict[str, Any] = {}

    def fake_consumer(*_topics: str, **options: Any) -> _FakeConsumer:
        passed.update(options)
        return _FakeConsumer()

    monkeypatch.setattr(updates_module, "AIOKafkaConsumer", fake_consumer)

    kafka_consumer("broker:9092", TOPIC)

    assert passed["auto_offset_reset"] == "latest"


async def test_이_프로세스의_갱신은_브로커를_거치지_않고_청취자에_닿는다() -> None:
    heard: list[str] = []
    subscriber = UpdateSubscriber("broker:9092", TOPIC, lambda _b, _t: _FakeConsumer())
    unsubscribe = subscriber.subscribe("e1", lambda: heard.append("e1"))

    subscriber.notify("e1")
    unsubscribe()
    subscriber.notify("e1")

    assert heard == ["e1"]

    await subscriber.close()
