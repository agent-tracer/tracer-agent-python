"""갱신이 있었다는 사실만 다른 프로세스에 흘려 보내는 발행 진입점을 소유한다."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from aiokafka import AIOKafkaProducer

_log = logging.getLogger(__name__)

# 브로커가 죽어 있을 때 실행이 그만큼 서 있지 않도록 드라이버 기본 대기보다 짧게 끊는다.
PUBLISH_TIMEOUT_S = 5.0


class MessageProducer(Protocol):
    """토픽 하나에 메시지를 보내는 생산자다."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> Any: ...


class ProducerFactory(Protocol):
    """브로커 주소를 받아 생산자 하나를 만든다."""

    def __call__(self, brokers: str) -> MessageProducer: ...


def kafka_producer(brokers: str) -> MessageProducer:
    """설정한 브로커에 붙는 Kafka 생산자를 만든다."""
    producer: MessageProducer = AIOKafkaProducer(bootstrap_servers=brokers)
    return producer


class UpdatePublisher:
    """갱신 사실을 식별자만 담아 알리며 보내지 못해도 부른 쪽을 멈추지 않는다."""

    def __init__(self, brokers: str, topic: str, factory: ProducerFactory = kafka_producer) -> None:
        self._brokers = brokers
        self._topic = topic
        self._factory = factory
        self._producer: MessageProducer | None = None
        self._lock = asyncio.Lock()

    async def publish(self, key: str, payload: dict[str, Any]) -> bool:
        """갱신 사실 하나를 토픽에 흘리고 보냈는지 낸다."""
        try:
            producer = await self._started()
            await asyncio.wait_for(
                producer.send_and_wait(
                    self._topic, json.dumps(payload, ensure_ascii=False).encode(), key.encode()
                ),
                PUBLISH_TIMEOUT_S,
            )
        except Exception as error:
            # 깨우지 못한 구독자는 다음 조회에서 따라잡으므로 실행을 여기서 끊지 않는다.
            _log.warning("update wakeup publish failed: %s", error)
            return False
        return True

    async def close(self) -> None:
        """생산자를 연 적이 있으면 닫는다."""
        async with self._lock:
            producer = self._producer
            self._producer = None
        if producer is not None:
            await producer.stop()

    async def _started(self) -> MessageProducer:
        async with self._lock:
            producer = self._producer
            if producer is None:
                producer = self._factory(self._brokers)
                await producer.start()
                self._producer = producer
            return producer
