"""규칙 생성의 근거 이벤트를 그 사용자 범위로 읽는 창구를 검증한다."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tracer_agent.shared.workflows.jobs_anchor import (
    RuleAnchorClient,
    RuleAnchorSource,
    RuleAnchorUnavailable,
)

BASE_URL = "http://tracer-api:3902"

EVENT: dict[str, Any] = {
    "id": "ev-1",
    "taskId": "task-1",
    "kind": "agent_tracer.user.message",
}


def client(handler: Any) -> RuleAnchorSource:
    transport = httpx.MockTransport(handler)
    return RuleAnchorClient(httpx.AsyncClient(transport=transport), BASE_URL)


async def test_근거의_태스크와_발화_여부를_가른다() -> None:
    seen: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["x-monitor-user"]))
        return httpx.Response(200, json={"ok": True, "data": {"event": EVENT}})

    anchor = await client(respond).find("user-1", "ev-1")

    assert seen == [(f"{BASE_URL}/api/v1/events/ev-1", "user-1")]
    assert anchor is not None
    assert (anchor.id, anchor.task_id, anchor.user_message) == ("ev-1", "task-1", True)


async def test_사용자_발화가_아닌_이벤트도_그대로_읽는다() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        event = {**EVENT, "kind": "agent_tracer.tool.call"}
        return httpx.Response(200, json={"ok": True, "data": {"event": event}})

    anchor = await client(respond).find("user-1", "ev-1")

    assert anchor is not None
    assert anchor.user_message is False


async def test_남의_근거는_없는_것으로_본다() -> None:
    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"ok": False, "error": {"code": "not_found"}})

    assert await client(missing).find("user-1", "ev-1") is None


async def test_창구가_흔들리면_판정을_지어내지_않는다() -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with pytest.raises(RuleAnchorUnavailable):
        await client(down).find("user-1", "ev-1")


async def test_봉투_밖의_답은_판정으로_읽지_않는다() -> None:
    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"event": EVENT})

    with pytest.raises(RuleAnchorUnavailable):
        await client(malformed).find("user-1", "ev-1")


async def test_이벤트가_없는_본문은_없는_근거로_본다() -> None:
    def empty(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "data": {}})

    assert await client(empty).find("user-1", "ev-1") is None
