"""chat 기억 도구가 확인 대기 없이 tracer-api 기억 API를 즉시 부르는 HTTP 진입점을 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .bindings import GATE_NONE, TOOL_BINDINGS, fill_path
from .reader import scoped_headers, unwrap_envelope

RECALL_TOOL = "recall_facts"
REMEMBER_TOOL = "remember_fact"


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

    async def remember(self, key: str, content: str) -> ChatMemoryResult:
        """remember_fact 바인딩대로 사실 하나를 턴이 끝나기를 기다리지 않고 즉시 적재한다."""
        return await self._call(REMEMBER_TOOL, {"key": key, "content": content})

    async def _call(self, tool_name: str, args: dict[str, object]) -> ChatMemoryResult:
        binding = TOOL_BINDINGS[tool_name]
        if binding.gate != GATE_NONE:
            raise ValueError(f"{tool_name} is not an unapproved memory tool")
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
