"""task-cleanup이 낸 제안을 정리 제안 창구로 배달한다."""

from __future__ import annotations

from typing import Any

from ..runtime.outputs import object_items, post_output
from ..runtime.tracer_client import TracerApiPort

CLEANUP_SUGGESTIONS_PATH = "/api/v1/task-cleanup/suggestions"

# 창구가 한 번에 받는 항목 수이며 계약이 그 상한을 적는다.
MAX_SUGGESTIONS = 50


async def deliver_suggestions(tracer: TracerApiPort, execution_id: str, data: dict[str, Any]) -> None:
    """스캔이 세운 정리 제안을 창구로 보내며 제안이 없으면 부르지 않는다."""
    suggestions = object_items(data, "suggestions")
    if not suggestions:
        return
    body = {"suggestions": suggestions[:MAX_SUGGESTIONS], "jobId": execution_id}
    await post_output(tracer, CLEANUP_SUGGESTIONS_PATH, body, execution_id)
