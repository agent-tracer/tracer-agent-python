"""다른 프로세스가 보낸 실행 갱신 신호를 열린 연결마다의 알림으로 나눈다."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

from aiokafka import AIOKafkaConsumer

_log = logging.getLogger(__name__)

# 신호를 놓쳐도 주기 조회가 정본을 다시 읽으므로 지난 신호를 되짚지 않는다.
_OFFSET_RESET = "latest"


class ChatExecutionUpdates(Protocol):
    """실행 하나가 바뀌었다는 신호를 듣는 창구다."""

    def subscribe(self, execution_id: str, listener: Callable[[], None]) -> Callable[[], None]:
        """이 실행의 갱신을 듣기 시작하고 그만 듣는 방법을 낸다."""
        ...


class ConsumerFactory(Protocol):
    """브로커 주소와 토픽을 받아 소비자 하나를 만든다."""

    def __call__(self, brokers: str, topic: str) -> Any: ...


def kafka_consumer(brokers: str, topic: str) -> Any:
    """설정한 브로커에 연결되는 Kafka 소비자를 만든다."""
    return AIOKafkaConsumer(topic, bootstrap_servers=brokers, auto_offset_reset=_OFFSET_RESET)


class UpdateSubscriber:
    """토픽 하나를 한 번만 듣고 실행 식별자마다의 청취자에게 나눠 준다."""

    def __init__(self, brokers: str, topic: str, factory: ConsumerFactory = kafka_consumer) -> None:
        self._brokers = brokers
        self._topic = topic
        self._factory = factory
        self._listeners: dict[str, set[Callable[[], None]]] = {}
        self._consumer: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def subscribe(self, execution_id: str, listener: Callable[[], None]) -> Callable[[], None]:
        """이 실행의 갱신을 듣기 시작하고 그만 듣는 방법을 낸다."""
        self._listeners.setdefault(execution_id, set()).add(listener)
        started = asyncio.ensure_future(self._start())

        def unsubscribe() -> None:
            started.cancel()
            listeners = self._listeners.get(execution_id)
            if listeners is None:
                return
            listeners.discard(listener)
            if not listeners:
                self._listeners.pop(execution_id, None)

        return unsubscribe

    async def close(self) -> None:
        """듣기 시작한 적이 있으면 그만 듣는다."""
        async with self._lock:
            task, consumer = self._task, self._consumer
            self._task, self._consumer = None, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if consumer is not None:
            await consumer.stop()

    async def _start(self) -> None:
        async with self._lock:
            if self._consumer is not None:
                return
            consumer = self._factory(self._brokers, self._topic)
            try:
                await consumer.start()
            except Exception as unreachable:
                # 브로커가 없어도 주기 조회가 정본을 실어 보내므로 연결을 여기서 끊지 않는다.
                _log.warning("chat execution update subscribe failed: %s", unreachable)
                return
            self._consumer = consumer
            self._task = asyncio.ensure_future(self._listen(consumer))

    async def _listen(self, consumer: Any) -> None:
        async for record in consumer:
            for listener in tuple(self._listeners.get(_execution_id(record), ())):
                listener()


def _execution_id(record: Any) -> str:
    key = record.key
    if isinstance(key, bytes | bytearray):
        return key.decode()
    try:
        payload = json.loads(record.value or b"null")
    except ValueError:
        return ""
    return str(payload.get("executionId", "")) if isinstance(payload, dict) else ""
