"""recipe-scan이 추적 창구에서 태스크와 이벤트와 규칙을 읽는 사용자 범위 진입점을 소유한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tracer_agent.shared.agents.shared.json_view import (
    JsonObject,
    as_object,
    as_objects,
)

from ..runtime.scoped_event_reader import (
    OPTIONAL_EVENT_KEYS,
    event_page,
    read_event_window,
    slim_event,
    timeline_path,
)
from ..runtime.tracer_client import TracerApiPort

RULES_PATH = "/api/v1/rules"
# 실행에 적용되는 규칙만 인용할 수 있으므로 승인 대기 상태는 목록에서 걸러낸다.
ACTIVE_REVIEW_STATE = "active"


def task_path(task_id: str) -> str:
    """태스크 하나의 조회 창구 경로다."""
    return f"/api/v1/tasks/{task_id}"


# 후보가 turn 을 인용하므로 이 축은 turnId 까지 모델에게 보인다.
SLIM_EVENT_KEYS: tuple[str, ...] = ("turnId", *OPTIONAL_EVENT_KEYS)


def slim_recipe_event(item: JsonObject) -> JsonObject:
    """이 축이 모델에게 내줄 이벤트 표현으로 줄인다."""
    return slim_event(item, SLIM_EVENT_KEYS)


@dataclass(frozen=True)
class TaskEventWindow:
    """요약이 보는 태스크 하나와 그 앞쪽 이벤트 창과 전체 건수다."""

    task: JsonObject
    rows: list[JsonObject]
    total: int


class RecipeLedgerReader:
    """한 사용자의 추적 창구만 읽도록 생성 시점에 범위가 묶인 조회 진입점이다."""

    def __init__(self, tracer: TracerApiPort) -> None:
        self._tracer = tracer

    async def task_with_events(self, task_id: str, window: int) -> TaskEventWindow | None:
        """요약을 만들 태스크와 앞쪽 이벤트 창과 전체 건수를 함께 읽는다."""
        detail = await self._tracer.get(task_path(task_id))
        if detail is None:
            return None
        read = await read_event_window(self._tracer, task_id, window)
        if read is None:
            return None
        rows, total = read
        return TaskEventWindow(as_object(as_object(detail)["task"]), rows, total)

    async def task_events(
        self, task_id: str, limit: int, cursor: str | None, order: Literal["asc", "desc"]
    ) -> JsonObject | None:
        """태스크 이벤트 한 페이지를 읽으며 소유하지 않은 태스크에는 아무것도 돌려주지 않는다."""
        payload = await self._tracer.get(
            timeline_path(task_id), {"limit": limit, "cursor": cursor, "order": order}
        )
        if payload is None:
            return None
        return event_page(as_object(payload), slim_recipe_event)

    async def applicable_rules(self, task_id: str) -> list[JsonObject]:
        """태스크에 적용되는 살아 있는 규칙만 읽는다."""
        payload = await self._tracer.get(RULES_PATH, {"taskId": task_id})
        if payload is None:
            return []
        return [
            _slim_rule(item)
            for item in as_objects(as_object(payload).get("items"))
            if item.get("reviewState") == ACTIVE_REVIEW_STATE
        ]


def _slim_rule(item: JsonObject) -> JsonObject:
    return {
        "id": item["id"],
        "name": item["name"],
        "expect": _expect_view(as_object(item.get("expectation") or {})),
        "taskId": item["taskId"],
        "anchorEventId": item.get("anchorEventId"),
        "source": item["source"],
        "severity": item["severity"],
        "rationale": item.get("rationale"),
        "signature": item.get("signature"),
        "createdAt": item["createdAt"],
    }


def _expect_view(expectation: JsonObject) -> JsonObject:
    kind = expectation.get("kind")
    if kind == "command":
        return {"kind": kind, "commandMatches": expectation.get("commandMatches")}
    view: JsonObject = {"kind": kind}
    if kind == "pattern":
        view["pattern"] = expectation.get("pattern")
        if expectation.get("tool") is not None:
            view["action"] = expectation["tool"]
        return view
    view["action"] = expectation.get("tool")
    return view
