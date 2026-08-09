"""태스크를 소유한 추적에 조건부 보관을 요청하는 창구를 소유한다."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from urllib.parse import quote

from ..shared.instant import opt_iso
from ..shared.tracer_window import TracerWindow

ARCHIVE_PATH = "/api/v1/tasks/{task_id}/archive"


class CleanupTaskArchiver(Protocol):
    """조건이 깨지면 추적이 낸 거절을 그대로 올리는 보관 창구다."""

    async def archive(self, user_id: str, task_id: str, if_no_activity_since: datetime | None) -> None:
        """그 태스크의 보관을 조건과 함께 요청한다."""
        ...


class TracerTaskArchiver:
    """추적의 조건부 보관 창구를 부르며 거절의 상태와 코드는 그대로 올라간다."""

    def __init__(self, tracer: TracerWindow) -> None:
        self._tracer = tracer

    async def archive(self, user_id: str, task_id: str, if_no_activity_since: datetime | None) -> None:
        """제안이 관측한 마지막 사건 시각을 조건으로 실어 보관을 요청한다."""
        await self._tracer.request(
            "POST",
            ARCHIVE_PATH.format(task_id=quote(task_id, safe="")),
            user_id,
            body={"ifNoActivitySince": opt_iso(if_no_activity_since)},
        )
