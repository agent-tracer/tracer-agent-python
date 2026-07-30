"""recipe-scan이 추적 창구에서 태스크와 이벤트와 규칙을 읽는 사용자 범위 진입점을 소유한다."""

from __future__ import annotations

from typing import Any, Literal

from ..runtime.scoped_event_reader import event_page, read_event_window, timeline_path
from ..runtime.tracer_client import TracerApiClient

RULES_PATH = "/api/v1/rules"
# 실행에 적용되는 규칙만 인용할 수 있으므로 승인 대기 상태는 목록에서 걸러낸다.
ACTIVE_REVIEW_STATE = "active"
_SLIM_KEYS = ("turnId", "body", "toolName")


def task_path(task_id: str) -> str:
    """태스크 하나의 조회 창구 경로다."""
    return f"/api/v1/tasks/{task_id}"


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


class RecipeLedgerReader:
    """한 사용자의 추적 창구만 읽도록 생성 시점에 범위가 묶인 조회 진입점이다."""

    def __init__(self, tracer: TracerApiClient) -> None:
        self._tracer = tracer

    async def task_with_events(self, task_id: str, window: int) -> dict[str, Any] | None:
        """요약을 만들 태스크와 앞쪽 이벤트 창과 전체 건수를 함께 읽는다."""
        detail = await self._tracer.get(task_path(task_id))
        if detail is None:
            return None
        read = await read_event_window(self._tracer, task_id, window)
        if read is None:
            return None
        rows, total = read
        return {"task": detail["task"], "rows": rows, "total": total}

    async def task_events(
        self, task_id: str, limit: int, cursor: str | None, order: Literal["asc", "desc"]
    ) -> dict[str, Any] | None:
        """태스크 이벤트 한 페이지를 읽으며 소유하지 않은 태스크에는 아무것도 돌려주지 않는다."""
        payload = await self._tracer.get(
            timeline_path(task_id), {"limit": limit, "cursor": cursor, "order": order}
        )
        if payload is None:
            return None
        return event_page(payload, slim_event)

    async def applicable_rules(self, task_id: str) -> list[dict[str, Any]]:
        """태스크에 적용되는 살아 있는 규칙만 읽는다."""
        payload = await self._tracer.get(RULES_PATH, {"taskId": task_id})
        if payload is None:
            return []
        return [
            _slim_rule(item)
            for item in payload.get("items") or []
            if item.get("reviewState") == ACTIVE_REVIEW_STATE
        ]


def _slim_rule(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "expect": _expect_view(item.get("expectation") or {}),
        "taskId": item["taskId"],
        "anchorEventId": item.get("anchorEventId"),
        "source": item["source"],
        "severity": item["severity"],
        "rationale": item.get("rationale"),
        "signature": item.get("signature"),
        "createdAt": item["createdAt"],
    }


def _expect_view(expectation: dict[str, Any]) -> dict[str, Any]:
    kind = expectation.get("kind")
    if kind == "command":
        return {"kind": kind, "commandMatches": expectation.get("commandMatches")}
    view: dict[str, Any] = {"kind": kind}
    if kind == "pattern":
        view["pattern"] = expectation.get("pattern")
        if expectation.get("tool") is not None:
            view["action"] = expectation["tool"]
        return view
    view["action"] = expectation.get("tool")
    return view
