"""승인된 도구 호출이 계약이 선언한 자리로 나가는지 검증한다(네트워크 없음)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tracer_agent.shared.agents.chat.surface.tool_client import (
    ChatToolFailed,
    HttpChatToolExecutor,
)

BASE_URL = "http://tracer-api.test"
AGENT_BASE_URL = "http://agent-api.test"


def _executor(handler: Any) -> tuple[HttpChatToolExecutor, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpChatToolExecutor(client, BASE_URL, AGENT_BASE_URL), client


async def test_경로에_실린_인자는_본문에_다시_싣지_않는다() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "data": {}})

    executor, client = _executor(handle)
    async with client:
        sentence = await executor.execute("local", "archive_task", {"taskId": "task-1"})

    assert seen == [("POST", "/api/v1/tasks/task-1/archive", {})]
    assert sentence == "Archived task task-1."


async def test_실행이_못박은_상수를_본문에_함께_싣는다() -> None:
    seen: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "data": {}})

    executor, client = _executor(handle)
    async with client:
        await executor.execute("local", "create_memo", {"taskId": "task-1", "body": "메모"})

    assert seen == [{"taskId": "task-1", "body": "메모", "author": "agent"}]


async def test_요청자는_사용자_헤더로_실린다() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-monitor-user"))
        return httpx.Response(200, json={"ok": True, "data": {}})

    executor, client = _executor(handle)
    async with client:
        await executor.execute("u2", "archive_task", {"taskId": "task-1"})

    assert seen == ["u2"]


async def test_상류가_거절하면_대기_행을_닫지_못한다() -> None:
    executor, client = _executor(lambda _request: httpx.Response(403, json={"ok": False}))

    async with client:
        with pytest.raises(ChatToolFailed):
            await executor.execute("local", "archive_task", {"taskId": "task-1"})


async def test_결과_문장은_봉투를_벗긴_본문을_인용한다() -> None:
    executor, client = _executor(
        lambda _request: httpx.Response(200, json={"ok": True, "data": {"reevaluated": 2}})
    )

    async with client:
        sentence = await executor.execute("local", "reevaluate_rule", {"ruleId": "rule-1"})

    assert sentence == "Reevaluated rule rule-1 over 2 event(s)."


async def test_잡_접수는_에이전트_서비스_자신을_부른다() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.scheme}://{request.url.host}")
        return httpx.Response(200, json={"ok": True, "data": {"job": {"id": "j1", "status": "queued"}}})

    executor, client = _executor(handle)
    async with client:
        await executor.execute("local", "enqueue_job", {"kind": "title.suggestion", "input": "{}"})

    assert seen == [AGENT_BASE_URL]
