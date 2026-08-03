"""chat 읽기 도구가 추적 API를 사용자 범위로 되읽는 HTTP 진입점을 소유한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from tracer_agent.shared.agents.chat.tools.bindings import TOOL_BINDINGS, fill_path
from tracer_agent.shared.agents.chat.tools.surface import READ_SURFACES, tool_surface
from tracer_agent.shared.agents.shared.json_view import JsonObject

# 창구가 요청자를 식별하는 헤더이며 실행 범위 토큰이 있으면 서버가 이 값을 토큰의 것으로 덮는다.
USER_HEADER = "x-monitor-user"


@dataclass(frozen=True)
class ChatReadResult:
    """읽기 API 한 번의 결과이며, 성공이면 봉투를 벗긴 본문이 text에 담긴다."""

    ok: bool
    status_code: int
    text: str


def scoped_headers(user_id: str, scope_token: str | None) -> dict[str, str]:
    """이 사용자에 매인 자격을 실어 보내는 헤더를 만든다."""
    headers = {USER_HEADER: user_id}
    # 토큰이 있으면 서버가 자기신고 헤더 대신 토큰이 담은 사용자를 믿는다.
    if scope_token is not None:
        headers["authorization"] = f"Bearer {scope_token}"
    return headers


def unwrap_envelope(raw: str) -> str:
    """성공 봉투를 벗겨 모델이 두 구현체에서 같은 필드를 보게 한다."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        return json.dumps(payload["data"], ensure_ascii=False)
    return raw


class ChatReadClient:
    """한 사용자의 읽기 API만 되읽도록 생성 시점에 범위가 묶인 HTTP 조회 진입점이다."""

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

    async def read(self, tool_name: str, args: JsonObject) -> ChatReadResult:
        """도구 이름에 맞는 읽기 API를 실행 범위 자격과 함께 되읽는다."""
        binding = TOOL_BINDINGS[tool_name]
        if tool_surface(tool_name) not in READ_SURFACES:
            raise ValueError(f"{tool_name} is not a read tool")
        params: dict[str, Any] = {
            binding.query[key]: value
            for key, value in args.items()
            if value is not None and key in binding.query
        }
        response = await self._client.get(
            f"{self._base_url}{fill_path(binding, args)}",
            params=params,
            headers=scoped_headers(self._user_id, self._scope_token),
        )
        if response.status_code >= 400:
            return ChatReadResult(ok=False, status_code=response.status_code, text=response.text)
        return ChatReadResult(ok=True, status_code=response.status_code, text=unwrap_envelope(response.text))
