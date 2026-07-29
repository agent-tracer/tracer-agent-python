"""발행 진입점이 브로커 없이 돌도록 메시지를 모아 두는 생산자 대역이다."""

from __future__ import annotations

from typing import Any


class RecordingProducer:
    """보낸 메시지를 그대로 모아 두는 발행 생산자 대역이다."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.sent: list[tuple[str, bytes, bytes]] = []
        self.starts = 0
        self.stops = 0
        self._failure = failure

    async def start(self) -> None:
        """생산자를 연 횟수를 센다."""
        self.starts += 1

    async def stop(self) -> None:
        """생산자를 닫은 횟수를 센다."""
        self.stops += 1

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> Any:
        """메시지를 모으거나 준비된 실패를 낸다."""
        if self._failure is not None:
            raise self._failure
        self.sent.append((topic, value, key))
        return None
