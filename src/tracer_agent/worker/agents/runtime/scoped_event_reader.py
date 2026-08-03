"""사용자 범위가 묶인 공통 이벤트 조회를 추적 창구에서 제공한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from tracer_agent.shared.agents.shared.json_view import (
    JsonObject,
    JsonValue,
    as_int,
    as_object,
    as_objects,
    opt_text,
    text_list,
)

from .tracer_client import TracerApiClient

EventView = Callable[[JsonObject], JsonObject]
# 타임라인 창구가 한 장에 내주는 상한이며 그보다 넓은 창은 여러 장으로 읽는다.
TIMELINE_PAGE_LIMIT = 500
# 값이 있을 때만 싣는 칸이며 어느 칸을 볼지는 부르는 쪽이 정한다.
OPTIONAL_EVENT_KEYS: tuple[str, ...] = ("body", "toolName")


def timeline_path(task_id: str) -> str:
    """한 태스크의 타임라인 창구 경로다."""
    return f"/api/v1/tasks/{task_id}/timeline"


def slim_event(item: JsonObject, optional_keys: tuple[str, ...] = OPTIONAL_EVENT_KEYS) -> JsonObject:
    """모델에게 내줄 이벤트 표현으로 줄이며 값이 없는 필드는 싣지 않는다."""
    event: JsonObject = {
        "id": item["id"],
        "seq": str(item["seq"]),
        "kind": item["kind"],
        "title": item["title"],
        "filePaths": list(text_list(item.get("filePaths"))),
        "occurredAt": item["occurredAt"],
    }
    for key in optional_keys:
        if item.get(key) is not None:
            event[key] = item[key]
    return event


async def read_event_window(
    tracer: TracerApiClient, task_id: str, wanted: int
) -> tuple[list[JsonObject], int] | None:
    """이른 이벤트부터 원하는 만큼 여러 장에 걸쳐 읽고 전체 건수를 함께 낸다."""
    collected: list[JsonObject] = []
    total = 0
    cursor: str | None = None
    while len(collected) < wanted:
        params: dict[str, JsonValue] = {
            "limit": min(wanted - len(collected), TIMELINE_PAGE_LIMIT),
            "order": "asc",
            "cursor": cursor,
        }
        payload = await tracer.get(timeline_path(task_id), params)
        if payload is None:
            return None
        page = as_object(payload)
        items = as_objects(page.get("items"))
        total = as_int(page.get("total"), total + len(items))
        collected.extend(items)
        next_cursor = opt_text(page.get("nextCursor"))
        if not items or next_cursor is None:
            break
        cursor = next_cursor
    return collected[:wanted], total


def event_page(payload: JsonObject, view: EventView = slim_event) -> JsonObject:
    """타임라인 한 장을 도구가 내주는 페이지 모양으로 옮긴다."""
    items = as_objects(payload.get("items"))
    next_cursor = opt_text(payload.get("nextCursor"))
    page: JsonObject = {
        "events": [view(item) for item in items],
        "truncated": next_cursor is not None,
        "total": as_int(payload.get("total"), len(items)),
    }
    if next_cursor is not None:
        page["nextCursor"] = next_cursor
    return page


class ScopedEventReader:
    """한 사용자가 소유한 태스크의 이벤트만 페이지 단위로 읽는다."""

    def __init__(self, tracer: TracerApiClient) -> None:
        self._tracer = tracer

    async def task_events(
        self, task_id: str, limit: int, cursor: str | None, order: Literal["asc", "desc"]
    ) -> JsonObject | None:
        """태스크 이벤트 한 페이지를 읽으며 소유하지 않은 태스크에는 아무것도 돌려주지 않는다."""
        payload = await self._tracer.get(
            timeline_path(task_id), {"limit": limit, "cursor": cursor, "order": order}
        )
        if payload is None:
            return None
        return event_page(as_object(payload))
