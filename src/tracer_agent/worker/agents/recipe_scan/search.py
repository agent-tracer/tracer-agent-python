"""recipe-scan이 검색 색인을 읽는 사용자 범위 진입점을 소유한다."""

from __future__ import annotations

from typing import Any

from opensearchpy import AsyncOpenSearch

EVENTS_INDEX = "events"
TASKS_INDEX = "tasks"
RECIPES_INDEX = "recipes"


def _hits(response: dict[str, Any]) -> list[dict[str, Any]]:
    return list(response.get("hits", {}).get("hits", []))


def _total(response: dict[str, Any]) -> int | None:
    total = response.get("hits", {}).get("total")
    if isinstance(total, dict):
        value = total.get("value")
        return int(value) if isinstance(value, int) else None
    return int(total) if isinstance(total, int) else None


def _pick(source: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: source[name] for name in names if source.get(name) is not None}


class RecipeSearchReader:
    """한 사용자의 색인만 읽도록 생성 시점에 범위가 묶인 검색 진입점이다."""

    def __init__(self, client: AsyncOpenSearch, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    async def search_events(
        self,
        q: str,
        limit: int,
        offset: int,
        task_id: str | None,
        kind: str | None,
        tool_name: str | None,
    ) -> dict[str, Any]:
        """제목과 본문에서 이벤트를 찾아 최신순 한 페이지를 낸다."""
        conditions: list[dict[str, Any]] = [{"term": {"userId": self._user_id}}]
        for field, value in (("taskId", task_id), ("kind", kind), ("toolName", tool_name)):
            if value is not None:
                conditions.append({"term": {field: value}})
        body: dict[str, Any] = {
            "size": limit + 1,
            "track_total_hits": True,
            "sort": [{"occurredAt": "desc"}],
            "query": {
                "bool": {
                    "must": [{"multi_match": {"query": q, "fields": ["title", "body"]}}],
                    "filter": conditions,
                }
            },
        }
        if offset > 0:
            body["from"] = offset
        response = await self._client.search(index=EVENTS_INDEX, body=body)

        hits = _hits(response)
        truncated = len(hits) > limit
        events = [
            {
                "id": hit.get("_id", ""),
                **_pick(
                    hit.get("_source", {}),
                    "taskId",
                    "seq",
                    "kind",
                    "title",
                    "body",
                    "toolName",
                    "filePaths",
                    "occurredAt",
                ),
            }
            for hit in hits[:limit]
        ]
        return {"events": events, "truncated": truncated, "total": _total(response) or len(events)}

    async def similar_tasks(self, anchor_title: str, anchor_task_id: str, limit: int) -> list[dict[str, Any]]:
        """앵커와 제목이 닮은 다른 태스크를 찾는다."""
        response = await self._client.search(
            index=TASKS_INDEX,
            body={
                "size": limit,
                "query": {
                    "bool": {
                        "must": [{"more_like_this": {"fields": ["title"], "like": anchor_title}}],
                        "filter": [{"term": {"userId": self._user_id}}],
                        "must_not": [{"ids": {"values": [anchor_task_id]}}],
                    }
                },
            },
        )
        return [
            {
                "id": hit.get("_id", ""),
                **_pick(hit.get("_source", {}), "title", "status", "taskKind", "updatedAt"),
            }
            for hit in _hits(response)
        ]

    async def search_recipes(self, q: str, limit: int) -> list[dict[str, Any]]:
        """수정 대상이 될 수 있는 레시피를 찾는다."""
        response = await self._client.search(
            index=RECIPES_INDEX,
            body={
                "size": limit,
                "query": {
                    "bool": {
                        "must": [{"more_like_this": {"fields": ["title", "intent", "summaryMd"], "like": q}}],
                        "filter": [{"term": {"userId": self._user_id}}],
                    }
                },
            },
        )
        return [
            {
                "id": hit.get("_id", ""),
                **_pick(
                    hit.get("_source", {}), "title", "intent", "status", "userEdited", "rev", "updatedAt"
                ),
            }
            for hit in _hits(response)
        ]
