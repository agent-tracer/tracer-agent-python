"""task-cleanup 스캔이 훑는 창의 경계를 고정한다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tracer_agent.worker.agents.task_cleanup.reader import (
    TASK_PAGE_LIMIT,
    TASK_SCAN_LIMIT,
    load_cleanup_batch,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)
STALE = "2026-07-01T00:00:00Z"


def _task(index: int, origin: str) -> dict[str, Any]:
    # 이벤트가 없으면 no-events 하나로 후보가 되므로 스캔 경계만 남는다.
    return {
        "id": f"task-{index}",
        "title": f"제목 {index}",
        "status": "completed",
        "origin": origin,
        "lastEventAt": None,
        "updatedAt": STALE,
    }


class _PagedTracer:
    """목록 창구를 장 단위로 흉내 내고 자식 조회에는 빈 목록을 낸다."""

    def __init__(self, tasks: list[dict[str, Any]]) -> None:
        self._tasks = tasks
        self.read = 0

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path.endswith("/children"):
            return {"items": []}
        start = 0 if params is None or params.get("cursor") is None else int(params["cursor"])
        page = self._tasks[start : start + TASK_PAGE_LIMIT]
        self.read += len(page)
        nxt = start + TASK_PAGE_LIMIT
        return {"items": page, "nextCursor": str(nxt) if nxt < len(self._tasks) else None}


@pytest.mark.asyncio
async def test_상한보다_적으면_잘리지_않았다고_판정한다() -> None:
    tracer = _PagedTracer([_task(i, "claude-code") for i in range(TASK_SCAN_LIMIT)])

    batch = await load_cleanup_batch(tracer, NOW)  # type: ignore[arg-type]

    assert batch.batchTruncated is False
    assert len(batch.candidates) == TASK_SCAN_LIMIT


@pytest.mark.asyncio
async def test_원본이_상한을_넘으면_잘렸다고_판정한다() -> None:
    tracer = _PagedTracer([_task(i, "claude-code") for i in range(TASK_SCAN_LIMIT + 1)])

    batch = await load_cleanup_batch(tracer, NOW)  # type: ignore[arg-type]

    assert batch.batchTruncated is True
    assert len(batch.candidates) == TASK_SCAN_LIMIT


@pytest.mark.asyncio
async def test_서버_에이전트의_태스크는_자른_뒤에_뺀다() -> None:
    # 앞쪽 250개가 server-sdk여도 창을 넓히지 않으므로 사용자 태스크는 그만큼 적게 남는다.
    mixed = [_task(i, "server-sdk" if i < 250 else "claude-code") for i in range(250 + TASK_SCAN_LIMIT)]
    tracer = _PagedTracer(mixed)

    batch = await load_cleanup_batch(tracer, NOW)  # type: ignore[arg-type]

    assert batch.batchTruncated is True
    assert len(batch.candidates) == TASK_SCAN_LIMIT - 250


@pytest.mark.asyncio
async def test_상한을_채우면_그_뒤의_이력을_읽지_않는다() -> None:
    tracer = _PagedTracer([_task(i, "server-sdk") for i in range(TASK_SCAN_LIMIT * 2)])

    batch = await load_cleanup_batch(tracer, NOW)  # type: ignore[arg-type]

    assert batch.candidates == []
    assert batch.batchTruncated is True
    assert tracer.read <= TASK_SCAN_LIMIT + TASK_PAGE_LIMIT
