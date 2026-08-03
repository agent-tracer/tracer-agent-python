"""recipe-scan이 추적 창구의 검색을 읽는 사용자 범위 진입점을 소유한다."""

from __future__ import annotations

from tracer_agent.shared.agents.shared.json_view import JsonObject, JsonValue, as_objects, text

from ..runtime.tracer_client import TracerApiPort

EVENT_SEARCH_PATH = "/api/v1/events/search"
TASK_SEARCH_PATH = "/api/v1/tasks/search"
RECIPE_SEARCH_PATH = "/api/v1/recipes/search"
RECIPES_PATH = "/api/v1/recipes"
# 검색 창구는 이벤트 적중 뒤에 메모 적중을 이어 붙이며 메모만 이 표식을 갖는다.
MEMO_HIT_TYPE = "memo"

_EVENT_KEYS = ("taskId", "seq", "kind", "title", "body", "toolName", "filePaths", "occurredAt")
_TASK_KEYS = ("title", "status", "taskKind", "updatedAt")
_RECIPE_KEYS = ("title", "intent", "status", "userEdited", "rev", "updatedAt")


class RecipeSearchReader:
    """한 사용자의 검색 창구만 읽도록 생성 시점에 범위가 묶인 검색 진입점이다."""

    def __init__(self, tracer: TracerApiPort) -> None:
        self._tracer = tracer

    async def search_events(
        self,
        q: str,
        limit: int,
        offset: int,
        task_id: str | None,
        kind: str | None,
        tool_name: str | None,
    ) -> JsonObject:
        """제목과 본문에서 이벤트를 찾아 최신순 한 페이지를 낸다."""
        payload = await self._tracer.get(
            EVENT_SEARCH_PATH,
            {
                "q": q,
                "limit": limit + 1,
                "offset": offset or None,
                "taskId": task_id,
                "kind": kind,
                "toolName": tool_name,
            },
        )
        hits = _hits(payload)
        truncated = len(hits) > limit
        events: list[JsonValue] = [
            {"id": hit.get("id", ""), **_pick(hit, _EVENT_KEYS)} for hit in hits[:limit]
        ]
        return {"events": events, "truncated": truncated, "total": _total(payload, offset + len(events))}

    async def similar_tasks(self, anchor_title: str, anchor_task_id: str, limit: int) -> list[JsonObject]:
        """앵커와 제목이 닮은 다른 태스크를 찾는다."""
        payload = await self._tracer.get(TASK_SEARCH_PATH, {"q": anchor_title, "limit": limit + 1})
        similar = [hit for hit in _hits(payload) if hit.get("id") != anchor_task_id]
        return [{"id": hit.get("id", ""), **_pick(hit, _TASK_KEYS)} for hit in similar[:limit]]

    async def search_recipes(self, q: str, limit: int) -> list[JsonObject]:
        """수정 대상이 될 수 있는 레시피를 찾아 순위대로 원장 행을 낸다."""
        found = _items(await self._tracer.get(RECIPE_SEARCH_PATH, {"q": q, "limit": limit}))
        ranked = [text(hit["recipeId"]) for hit in found if isinstance(hit.get("recipeId"), str)]
        if not ranked:
            return []
        # 검색 창구는 판단에 필요한 값만 내므로 개정 근거가 되는 판은 원장 목록에서 읽는다.
        owned = {
            text(recipe["id"]): recipe
            for recipe in _items(await self._tracer.get(RECIPES_PATH))
            if isinstance(recipe.get("id"), str)
        }
        return [
            {"id": recipe_id, **_pick(owned[recipe_id], _RECIPE_KEYS)}
            for recipe_id in ranked
            if recipe_id in owned
        ]


def _items(payload: JsonValue) -> list[JsonObject]:
    if not isinstance(payload, dict):
        return []
    return as_objects(payload.get("items"))


def _hits(payload: JsonValue) -> list[JsonObject]:
    return [item for item in _items(payload) if item.get("hitType") != MEMO_HIT_TYPE]


def _pick(source: JsonObject, keys: tuple[str, ...]) -> JsonObject:
    return {key: source[key] for key in keys if source.get(key) is not None}


def _total(payload: JsonValue, counted: int) -> int:
    total = payload.get("total") if isinstance(payload, dict) else None
    return total if isinstance(total, int) else counted
