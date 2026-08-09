"""추적이 소유한 사건 스트림을 자기 소비자 그룹으로 읽어 투영에 넘긴다."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Protocol

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from ..runtime.ledger import SqlSource
from ..shared.json_view import JsonValue
from .projection import RecipeProjection, parse_ledger_record
from .store import RecipeApplicationStore
from .topic import ledger_events_consumer_group, ledger_events_topic

_log = logging.getLogger(__name__)

# 프로젝터는 붙은 뒤 마지막 반영 지점부터 이어 읽으므로 처음 붙을 때는 원장의 처음부터 읽는다.
_OFFSET_RESET = "earliest"


class LedgerConsumerFactory(Protocol):
    """브로커 주소와 토픽과 그룹을 받아 소비자 하나를 만든다."""

    def __call__(self, brokers: str, topic: str, group_id: str) -> Any: ...


def kafka_ledger_consumer(brokers: str, topic: str, group_id: str) -> Any:
    """설정한 브로커에 연결되는 Kafka 소비자를 만든다."""
    return AIOKafkaConsumer(
        topic,
        bootstrap_servers=brokers,
        group_id=group_id,
        auto_offset_reset=_OFFSET_RESET,
        enable_auto_commit=False,
    )


class LedgerEventConsumer:
    """사건 스트림을 읽어 투영을 부르며 원장 연결은 사건마다 빌린다."""

    def __init__(
        self,
        brokers: str,
        source: SqlSource,
        factory: LedgerConsumerFactory = kafka_ledger_consumer,
    ) -> None:
        self._brokers = brokers
        self._source = source
        self._factory = factory
        self._consumer: Any | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """소비자를 열고 사건을 읽기 시작하며 브로커에 닿지 못하면 창구를 멈추지 않는다."""
        if self._consumer is not None:
            return
        consumer = self._factory(self._brokers, ledger_events_topic(), ledger_events_consumer_group())
        try:
            await consumer.start()
        except (KafkaError, OSError) as unreachable:
            _log.warning("ledger.event_stream.unreachable error=%s", unreachable)
            return
        self._consumer = consumer
        self._task = asyncio.ensure_future(self._listen(consumer))

    async def stop(self) -> None:
        """읽기를 멈추고 소비자를 닫는다."""
        task, consumer = self._task, self._consumer
        self._task, self._consumer = None, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if consumer is not None:
            await consumer.stop()

    async def _listen(self, consumer: Any) -> None:
        async for message in consumer:
            if await self._handle(_parse_json(message.value)):
                await consumer.commit()
                continue
            # 배달이 적어도 한 번이므로 반영하지 못한 사건은 다시 배달되어 같은 투영을 다시 시도한다.
            await consumer.seek_to_committed()

    async def _handle(self, value: JsonValue) -> bool:
        """사건 하나를 투영하고 이 사건을 반영했는지 낸다."""
        record = parse_ledger_record(value)
        if record is None:
            _log.warning("ledger.event_stream.unreadable")
            return True
        try:
            async with self._source.connect() as sql:
                await RecipeProjection(RecipeApplicationStore(sql)).handle(record)
        except Exception as failed:
            _log.error("ledger.event_projection.failed error=%s", failed)
            return False
        return True


def _parse_json(value: bytes | None) -> JsonValue:
    if value is None:
        return None
    try:
        parsed: JsonValue = json.loads(value)
    except ValueError:
        return None
    return parsed
