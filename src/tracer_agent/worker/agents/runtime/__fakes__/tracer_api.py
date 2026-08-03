"""추적 창구 포트의 대역이며 부른 창구를 기록하고 경로마다 정해 둔 응답을 낸다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tracer_agent.shared.agents.shared.json_view import JsonValue

from ..tracer_client import TracerApiPort

_DEFAULT_TASK: dict[str, Any] = {
    "id": "t1",
    "userId": "user-1",
    "title": "x",
    "slug": "x",
    "status": "completed",
    "taskKind": "monitoring",
    "origin": "cli",
    "archived": False,
    "createdAt": "2026-07-14T00:00:00Z",
    "updatedAt": "2026-07-14T00:00:00Z",
}


class FakeTracerApi(TracerApiPort):
    """부른 창구를 기록하고 경로마다 캔 응답을 돌려주는 추적 API 대역이다."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        owned: bool = True,
        total: int | None = None,
        rules: list[dict[str, Any]] | None = None,
        task: dict[str, Any] | None = None,
        turns: list[dict[str, Any]] | None = None,
        children: list[dict[str, Any]] | None = None,
        tasks: list[dict[str, Any]] | None = None,
        hits: dict[str, list[dict[str, Any]]] | None = None,
        recipes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows or []
        self.owned = owned
        self.total = len(self.rows) if total is None else total
        self.rules = rules or []
        self.task = {**_DEFAULT_TASK, **(task or {})}
        self.turns = turns or []
        self.children = children or []
        self.tasks = tasks or []
        self.hits = hits or {}
        self.recipes = recipes or []
        self.calls: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> JsonValue:
        """부른 경로와 인자를 기억하고 그 경로의 캔 응답을 낸다."""
        self.calls.append({"path": path, "params": dict(params or {})})
        if path.startswith("/api/v1/tasks/") and not self.owned:
            return None
        if path.endswith("/timeline"):
            return self._timeline_page(params or {})
        if path.endswith("/turns"):
            return {"items": list(self.turns)}
        if path.endswith("/children"):
            return {"items": list(self.children)}
        if path == "/api/v1/tasks":
            return {"items": list(self.tasks), "total": len(self.tasks), "nextCursor": None}
        if path == "/api/v1/tasks/search":
            return {"items": list(self.hits.get("tasks", []))}
        if path == "/api/v1/events/search":
            return {"items": list(self.hits.get("events", []))}
        if path == "/api/v1/recipes/search":
            return {"items": list(self.hits.get("recipes", []))}
        if path == "/api/v1/recipes":
            return {"items": list(self.recipes)}
        if path == "/api/v1/rules":
            return {"items": list(self.rules)}
        return {"task": dict(self.task)}

    def _timeline_page(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """창구가 그러듯 커서 뒤부터 limit만큼 잘라 주고 남은 것이 있으면 다음 커서를 낸다."""
        limit = int(params.get("limit") or len(self.rows) or 1)
        rows = list(self.rows)
        if params.get("order") == "desc":
            rows.reverse()
        cursor = params.get("cursor")
        if cursor is not None:
            seen = [index for index, row in enumerate(rows) if str(row["seq"]) == str(cursor)]
            rows = rows[seen[0] + 1 :] if seen else []
        page = rows[:limit]
        remaining = len(rows) > limit
        next_cursor = str(page[-1]["seq"]) if remaining and page else None
        return {"items": page, "nextCursor": next_cursor, "total": self.total}

    async def post(self, path: str, body: Mapping[str, Any]) -> JsonValue:
        """보낸 본문을 기억하고 요청에 실린 항목 수만큼 원장 행을 낸다."""
        self.posts.append({"path": path, "body": body})
        if path == "/api/v1/recipes":
            return {"recipes": [{"id": f"recipe-{index}"} for index in range(len(body["recipes"]))]}
        return {"suggestions": [{"id": f"cleanup-{index}"} for index in range(len(body["suggestions"]))]}
