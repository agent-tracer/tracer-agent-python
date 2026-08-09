"""recipe-scan이 사건과 태스크는 추적에서, 레시피는 자기 축에서 검색하는 진입점을 소유한다."""

from __future__ import annotations

from tracer_agent.shared.agents.shared.json_view import JsonObject, JsonValue, as_objects, text

from ..runtime.tracer_client import TracerApiPort

EVENT_SEARCH_PATH = "/api/v1/events/search"
TASK_SEARCH_PATH = "/api/v1/tasks/search"
RECIPE_SEARCH_PATH = "/api/agent/recipes/search"
# 검색 창구는 이벤트 적중 뒤에 메모 적중을 이어 붙이며 메모만 이 표식을 갖는다.
MEMO_HIT_TYPE = "memo"

_EVENT_KEYS = ("taskId", "seq", "kind", "title", "body", "toolName", "filePaths", "occurredAt")
_TASK_KEYS = ("title", "status", "taskKind", "updatedAt")
# 얇은 적중에 없는 칸은 실리지 않으므로 상태는 빈 글자가 되고 사람이 고쳤다는 표시는 거짓이 된다.
_RECIPE_KEYS = ("title", "intent", "rev", "updatedAt")


class RecipeSearchReader:
    """사건과 태스크는 추적이 소유하고 레시피는 이 축이 소유하므로 창구를 나눠 부른다."""

    def __init__(self, tracer: TracerApiPort, agent: TracerApiPort) -> None:
        self._tracer = tracer
        self._agent = agent

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
        """수정 대상이 될 수 있는 레시피를 자기 축의 검색 창구에서 순위대로 찾는다."""
        found = _items(await self._agent.get(RECIPE_SEARCH_PATH, {"q": q, "limit": limit}))
        return [_slim_recipe(hit) for hit in found]


def _slim_recipe(hit: JsonObject) -> JsonObject:
    """적중 하나를 도구가 내는 얇은 레시피로 옮긴다."""
    identifier = hit.get("recipeId") if isinstance(hit.get("recipeId"), str) else hit.get("id")
    return {
        "id": text(identifier) if isinstance(identifier, str) else "",
        "status": text(hit["status"]) if isinstance(hit.get("status"), str) else "",
        "userEdited": hit.get("userEdited") is True,
        **_pick(hit, _RECIPE_KEYS),
    }


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
