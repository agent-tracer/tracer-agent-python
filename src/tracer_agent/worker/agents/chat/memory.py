"""chat 기억 도구가 확인 대기 없이 에이전트의 기억 API를 즉시 부르는 HTTP 진입점을 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from tracer_agent.shared.agents.chat.tools.bindings import TOOL_BINDINGS, fill_path
from tracer_agent.shared.agents.chat.tools.surface import (
    MEMORY_SURFACE,
    recall_tool_name,
    tool_surface,
)
from tracer_agent.shared.agents.shared.json_view import JsonObject

from .reader import scoped_headers, unwrap_envelope

RECALL_TOOL = recall_tool_name()


@dataclass(frozen=True)
class ChatMemoryResult:
    """기억 API 한 번의 결과이며, 성공이면 봉투를 벗긴 본문이 text에 담긴다."""

    ok: bool
    status_code: int
    text: str


class ChatMemoryClient:
    """한 사용자의 장기기억만 확인 대기 없이 즉시 읽고 쓰도록 생성 시점에 범위가 묶인 HTTP 진입점이다."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        user_id: str,
        scope_token: str | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_id = user_id
        self._scope_token = scope_token

    async def recall(self) -> ChatMemoryResult:
        """recall_facts 바인딩대로 이 사용자의 장기기억 전체를 되읽는다."""
        return await self._call(RECALL_TOOL, {})

    async def _call(self, tool_name: str, args: JsonObject) -> ChatMemoryResult:
        binding = TOOL_BINDINGS[tool_name]
        if tool_surface(tool_name) != MEMORY_SURFACE:
            raise ValueError(f"{tool_name} is not a memory tool")
        url = f"{self._base_url}{fill_path(binding, args)}"
        headers = scoped_headers(self._user_id, self._scope_token)
        body = {wire: args[arg] for arg, wire in binding.body.items() if args.get(arg) is not None}
        if body:
            response = await self._client.request(binding.method, url, json=body, headers=headers)
        else:
            response = await self._client.request(binding.method, url, headers=headers)
        if response.status_code >= 400:
            return ChatMemoryResult(ok=False, status_code=response.status_code, text=response.text)
        return ChatMemoryResult(
            ok=True, status_code=response.status_code, text=unwrap_envelope(response.text)
        )
