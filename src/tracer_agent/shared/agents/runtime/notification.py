"""잡 상태 전이를 사용자의 열린 화면에 알리는 발행 진입점을 소유한다."""

from __future__ import annotations

from typing import Any

from .wakeup import UpdatePublisher


class JobStatusNotifier:
    """잡 상태 전이를 알림 봉투에 담아 전송하며 보내지 못해도 실행을 멈추지 않는다."""

    def __init__(self, publisher: UpdatePublisher, notification_type: str) -> None:
        self._publisher = publisher
        self._type = notification_type

    async def job_updated(self, user_id: str, payload: dict[str, Any]) -> bool:
        """잡 하나의 상태 전이를 알리고 보냈는지 낸다."""
        return await self._publisher.publish(
            user_id,
            {"userId": user_id, "notification": {"type": self._type, "payload": payload}},
        )
