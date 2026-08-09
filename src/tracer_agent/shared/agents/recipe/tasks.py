"""레시피가 인용한 태스크의 제목을 추적의 집합 조회로 읽는다."""

from __future__ import annotations

from typing import Protocol

from ..shared.json_view import as_objects
from ..shared.tracer_window import TracerWindow

TASKS_PATH = "/api/v1/tasks"

# 목록 창구 한 장이 담는 최대 개수이며 계약의 tracer.tasks 케이스가 같은 수를 갖는다.
MAX_IDS_PER_CALL = 100


class RecipeTaskReader(Protocol):
    """인용된 태스크의 제목을 한 번에 읽는 창구이며 닿지 않는 식별자는 결과에서 빠진다."""

    async def titles_by_ids(self, user_id: str, ids: list[str]) -> dict[str, str]:
        """식별자를 제목으로 푸는 표를 낸다."""
        ...


class TracerTaskReader:
    """인용된 식별자를 모아 한 번에 물어 레시피 수만큼 왕복이 생기지 않게 한다."""

    def __init__(self, tracer: TracerWindow) -> None:
        self._tracer = tracer

    async def titles_by_ids(self, user_id: str, ids: list[str]) -> dict[str, str]:
        """식별자를 제목으로 푸는 표를 내며 상한을 넘으면 나눠 부른다."""
        titles: dict[str, str] = {}
        for index in range(0, len(ids), MAX_IDS_PER_CALL):
            chunk = ids[index : index + MAX_IDS_PER_CALL]
            payload = await self._tracer.request("GET", TASKS_PATH, user_id, query={"ids": ",".join(chunk)})
            items = payload.get("items") if isinstance(payload, dict) else None
            for item in as_objects(items):
                task_id = item.get("id")
                title = item.get("title")
                if isinstance(task_id, str) and isinstance(title, str):
                    titles[task_id] = title
        return titles
