"""recipe-scan이 낸 후보를 레시피 창구로 배달한다."""

from __future__ import annotations

from typing import Any

from ..runtime.outputs import AGENT_AUTHOR, object_items, post_output
from ..runtime.tracer_client import TracerApiClient

RECIPES_PATH = "/api/v1/recipes"

# 창구가 한 번에 받는 항목 수이며 계약이 그 상한을 적는다.
MAX_RECIPES = 20


async def deliver_recipes(tracer: TracerApiClient, execution_id: str, data: dict[str, Any]) -> None:
    """스캔이 세운 레시피 후보를 창구로 보내며 후보가 없으면 부르지 않는다."""
    recipes = object_items(data, "recipes")
    if not recipes:
        return
    body = {
        "recipes": recipes[:MAX_RECIPES],
        "author": AGENT_AUTHOR,
        "sourceJobId": execution_id,
    }
    await post_output(tracer, RECIPES_PATH, body, execution_id)
