"""chat 읽기 진입점이 바인딩대로 API를 되읽고 봉투를 벗기는지 검증한다."""

from __future__ import annotations

import json

import httpx
import pytest

from tracer_agent.worker.agents.chat.reader import USER_HEADER, ChatReadClient

BASE_URL = "http://tracer-api:3902"


def _client(payload: object = None, status_code: int = 200) -> tuple[ChatReadClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    body = json.dumps({"ok": True, "data": payload}) if status_code < 400 else '{"ok":false}'

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, text=body)

    transport = httpx.MockTransport(handle)
    return ChatReadClient(httpx.AsyncClient(transport=transport), BASE_URL, "user-1"), seen


async def test_경로_인자와_쿼리_인자를_바인딩대로_갈라_보낸다() -> None:
    client, seen = _client({"items": []})

    await client.read("get_timeline", {"taskId": "task-1", "limit": 50, "cursor": None})

    assert str(seen[0].url) == f"{BASE_URL}/api/v1/tasks/task-1/timeline?limit=50"


async def test_바인딩에_없는_인자는_쿼리로_새지_않는다() -> None:
    client, seen = _client({"items": []})

    await client.read("list_tags", {"userId": "someone-else"})

    assert str(seen[0].url) == f"{BASE_URL}/api/v1/tags"


async def test_성공_응답의_봉투를_벗겨_두_백엔드가_같은_필드를_본다() -> None:
    client, _ = _client({"items": [], "total": 0, "nextCursor": None})

    result = await client.read("search_tasks", {})

    assert result.ok
    assert json.loads(result.text) == {"items": [], "total": 0, "nextCursor": None}


async def test_오류_응답은_봉투를_벗기지_않고_실패로_알린다() -> None:
    client, _ = _client(None, status_code=404)

    result = await client.read("get_task", {"taskId": "missing"})

    assert not result.ok
    assert result.status_code == 404


async def test_범위_토큰이_있으면_자기신고_헤더와_함께_실어_보낸다() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text='{"ok":true,"data":{}}')

    client = ChatReadClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handle)), BASE_URL, "user-1", "ms1.token"
    )

    await client.read("list_settings", {})

    assert seen[0].headers["authorization"] == "Bearer ms1.token"
    assert seen[0].headers[USER_HEADER] == "user-1"


async def test_쓰기_도구는_읽기_진입점으로_부를_수_없다() -> None:
    client, _ = _client({})

    with pytest.raises(ValueError):
        await client.read("delete_task", {"taskId": "task-1"})
