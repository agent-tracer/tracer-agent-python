"""스캔 앵커를 추적 창구에서 읽는 왕복을 본다."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tracer_agent.shared.workflows.jobs_anchor import (
    AnchorUnavailable,
    ScanAnchorClient,
    ScanAnchorSource,
)

BASE_URL = "http://tracer"
TASK = {
    "id": "task-1",
    "origin": "user",
    "root": True,
    "status": "completed",
}


def client(handler: Any) -> ScanAnchorSource:
    transport = httpx.MockTransport(handler)
    return ScanAnchorClient(httpx.AsyncClient(transport=transport), BASE_URL)


async def test_태스크의_자격_판정_값을_그_사용자_범위로_읽는다() -> None:
    seen: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["x-monitor-user"]))
        return httpx.Response(200, json={"ok": True, "data": {"task": TASK}})

    anchor = await client(respond).find("user-1", "task-1")

    assert seen == [(f"{BASE_URL}/api/v1/tasks/task-1", "user-1")]
    assert anchor is not None
    assert (anchor.id, anchor.root, anchor.status) == ("task-1", True, "completed")


async def test_남의_태스크는_없는_것으로_본다() -> None:
    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"ok": False, "error": {"code": "not_found"}})

    assert await client(missing).find("user-1", "task-1") is None


async def test_창구가_흔들리면_판정을_지어내지_않는다() -> None:
    def broken(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(AnchorUnavailable):
        await client(broken).find("user-1", "task-1")


async def test_봉투_밖의_답은_판정으로_읽지_않는다() -> None:
    def outside(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"task": TASK})

    with pytest.raises(AnchorUnavailable):
        await client(outside).find("user-1", "task-1")


async def test_태스크가_없는_본문은_없는_근거로_본다() -> None:
    def empty(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "data": {"task": None}})

    assert await client(empty).find("user-1", "task-1") is None
