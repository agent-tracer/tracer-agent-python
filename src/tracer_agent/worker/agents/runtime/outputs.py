"""잡이 낸 산출물을 추적 창구로 보내되 배달 실패가 종결을 되돌리지 않게 한다."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .tracer_client import TracerApiClient

_log = logging.getLogger(__name__)

# 에이전트가 만든 것임을 창구에 알리는 값이며 사람이 만든 것과 갈린다.
AGENT_AUTHOR = "agent"


async def post_output(tracer: TracerApiClient, path: str, body: Mapping[str, Any], execution_id: str) -> None:
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


def object_items(data: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """산출물 하나에서 창구가 받을 수 있는 객체 항목만 남긴다."""
    return [item for item in (data.get(key) or []) if isinstance(item, dict)]
