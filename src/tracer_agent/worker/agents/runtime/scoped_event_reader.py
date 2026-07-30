"""사용자 범위가 묶인 공통 이벤트 조회를 추적 창구에서 제공한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from .tracer_client import TracerApiClient

EventView = Callable[[dict[str, Any]], dict[str, Any]]
# 타임라인 창구가 한 장에 내주는 상한이며 그보다 넓은 창은 여러 장으로 읽는다.
TIMELINE_PAGE_LIMIT = 500
_SLIM_KEYS = ("body", "toolName")


def timeline_path(task_id: str) -> str:
    """한 태스크의 타임라인 창구 경로다."""
    return f"/api/v1/tasks/{task_id}/timeline"


def slim_event(item: dict[str, Any]) -> dict[str, Any]:
    """모델에게 내줄 이벤트 표현으로 줄이며 값이 없는 필드는 싣지 않는다."""
    event: dict[str, Any] = {
        "id": item["id"],
        "seq": str(item["seq"]),
        "kind": item["kind"],
        "title": item["title"],
        "filePaths": list(item.get("filePaths") or []),
        "occurredAt": item["occurredAt"],
    }
    for key in _SLIM_KEYS:
        if item.get(key) is not None:
            event[key] = item[key]
    return event


async def read_event_window(
    tracer: TracerApiClient, task_id: str, wanted: int
) -> tuple[list[dict[str, Any]], int] | None:
    """이른 이벤트부터 원하는 만큼 여러 장에 걸쳐 읽고 전체 건수를 함께 낸다."""
    collected: list[dict[str, Any]] = []
    total = 0
    cursor: str | None = None
    while len(collected) < wanted:
        params: dict[str, Any] = {
            "limit": min(wanted - len(collected), TIMELINE_PAGE_LIMIT),
            "order": "asc",
            "cursor": cursor,
        }
        payload = await tracer.get(timeline_path(task_id), params)
        if payload is None:
            return None
        items = list(payload.get("items") or [])
        total = int(payload.get("total") or total + len(items))
        collected.extend(items)
        next_cursor = payload.get("nextCursor")
        if not items or next_cursor is None:
            break
        cursor = str(next_cursor)
    return collected[:wanted], total


def event_page(payload: dict[str, Any], view: EventView = slim_event) -> dict[str, Any]:
    """타임라인 한 장을 도구가 내주는 페이지 모양으로 옮긴다."""
    items = list(payload.get("items") or [])
    next_cursor = payload.get("nextCursor")
    page: dict[str, Any] = {
        "events": [view(item) for item in items],
        "truncated": next_cursor is not None,
        "total": int(payload.get("total") or len(items)),
    }
    if next_cursor is not None:
        page["nextCursor"] = str(next_cursor)
    return page


class ScopedEventReader:
    """한 사용자가 소유한 태스크의 이벤트만 페이지 단위로 읽는다."""

    def __init__(self, tracer: TracerApiClient) -> None:
        self._tracer = tracer

    async def task_events(
        self, task_id: str, limit: int, cursor: str | None, order: Literal["asc", "desc"]
    ) -> dict[str, Any] | None:
        """태스크 이벤트 한 페이지를 읽으며 소유하지 않은 태스크에는 아무것도 돌려주지 않는다."""
        payload = await self._tracer.get(
            timeline_path(task_id), {"limit": limit, "cursor": cursor, "order": order}
        )
        if payload is None:
            return None
        return event_page(payload)
