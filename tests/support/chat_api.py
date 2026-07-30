"""테스트용 페이크: chat 도구가 부르는 확인 창구와 기억 API를 네트워크 없이 대신한다."""

from __future__ import annotations

import json as _json
from urllib.parse import unquote

import httpx

MEMORIES_PATH = "/api/agent/chat/memories"


def chat_confirmation_response(request: httpx.Request) -> httpx.Response:
    """확인 창구가 쓰기 도구 하나를 대기 행으로 세워 돌려주는 응답을 대신한다."""
    body = _json.loads(request.content)
    tool = str(body["toolName"])
    return httpx.Response(
        201,
        json={
            "ok": True,
            "data": {
                "confirmationId": f"conf-{tool}",
                "toolName": tool,
                "status": "pending",
                "summary": f"{tool}({body['args']})",
                "note": "Queued for user confirmation.",
            },
        },
    )


class FakeChatMemoryApi:
    """chat 기억 API가 즉시 적재하고 되읽어 주는 창구를 대신한다."""

    def __init__(self) -> None:
        self.facts: dict[str, str] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        """기억 API 요청은 직접 받고 나머지는 확인 창구로 넘긴다."""
        if not request.url.path.startswith(MEMORIES_PATH):
            return chat_confirmation_response(request)
        if request.method == "GET":
            return httpx.Response(200, json={"ok": True, "data": {"facts": self._rows()}})
        key = unquote(request.url.path[len(MEMORIES_PATH) :].lstrip("/"))
        content = str(_json.loads(request.content)["content"])
        self.facts[key] = content
        return httpx.Response(
            200,
            json={"ok": True, "data": {"key": key, "content": content, "status": "remembered"}},
        )

    def _rows(self) -> list[dict[str, str]]:
        return [
            {"key": key, "content": content, "updatedAt": "2026-01-01T00:00:00.000Z"}
            for key, content in self.facts.items()
        ]
