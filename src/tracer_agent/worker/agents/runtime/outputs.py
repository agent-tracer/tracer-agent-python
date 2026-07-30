"""잡 에이전트가 낸 산출물을 추적 창구 한 곳에 실어 보낸다."""

from __future__ import annotations

import logging
from typing import Any

from .tracer_client import TracerApiClient

_log = logging.getLogger(__name__)

RECIPES_PATH = "/api/v1/recipes"
CLEANUP_SUGGESTIONS_PATH = "/api/v1/task-cleanup/suggestions"

# 창구가 한 번에 받는 항목 수이며 계약이 그 상한을 적는다.
MAX_RECIPES = 20
MAX_SUGGESTIONS = 50

# 에이전트가 만든 것임을 창구에 알리는 값이며 사람이 만든 것과 갈린다.
AGENT_AUTHOR = "agent"


async def deliver_job_outputs(
    tracer: TracerApiClient, kind: str, execution_id: str, data: dict[str, Any] | None
) -> None:
    """종결한 잡의 산출물을 창구로 보내며 보낼 것이 없으면 아무 것도 부르지 않는다."""
    if not data:
        return
    if kind == "recipe-scan":
        await _post_recipes(tracer, execution_id, data)
        return
    if kind == "task-cleanup":
        await _post_suggestions(tracer, execution_id, data)


async def _post_recipes(tracer: TracerApiClient, execution_id: str, data: dict[str, Any]) -> None:
    recipes = [item for item in (data.get("recipes") or []) if isinstance(item, dict)]
    if not recipes:
        return
    body = {
        "recipes": recipes[:MAX_RECIPES],
        "author": AGENT_AUTHOR,
        "sourceJobId": execution_id,
    }
    await _post(tracer, RECIPES_PATH, body, execution_id)


async def _post_suggestions(tracer: TracerApiClient, execution_id: str, data: dict[str, Any]) -> None:
    suggestions = [item for item in (data.get("suggestions") or []) if isinstance(item, dict)]
    if not suggestions:
        return
    body = {"suggestions": suggestions[:MAX_SUGGESTIONS], "jobId": execution_id}
    await _post(tracer, CLEANUP_SUGGESTIONS_PATH, body, execution_id)


async def _post(tracer: TracerApiClient, path: str, body: dict[str, Any], execution_id: str) -> None:
    """창구가 자기 트랜잭션으로 한 벌을 쓰며, 실패하면 원장의 결과가 남고 산출물은 서지 않는다."""
    try:
        await tracer.post(path, body)
    except Exception as undelivered:
        _log.warning(
            "agent.job.output.undelivered path=%s executionId=%s reason=%s",
            path,
            execution_id,
            undelivered,
        )
