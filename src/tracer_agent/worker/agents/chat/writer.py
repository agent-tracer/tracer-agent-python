"""chat 쓰기 도구가 에이전트의 확인 창구에 대기 행을 세우는 HTTP 진입점을 소유한다."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from tracer_agent.shared.agents.chat.tools.surface import CONFIRM_SURFACE, tool_surface
from tracer_agent.shared.agents.shared.json_view import JsonObject

from .reader import scoped_headers, unwrapped_body

# 승인 대기 행을 세우는 창구이며 도구가 부를 API 자체는 승인된 뒤에 서버가 부른다.
CONFIRMATIONS_PATH = "/api/agent/chat/threads/{threadId}/confirmations"


@dataclass(frozen=True)
class ChatProposalResult:
    """확인 창구 한 번의 결과이며, 성공이면 봉투를 벗긴 본문이 text에 담긴다."""

    ok: bool
    status_code: int
    text: str
    confirmation_id: str
    # 이 실행에 창구가 아예 없을 때만 사유를 싣고, 창구가 거절한 실패의 사유는 상태가 만든다.
    unavailable: str | None = None

    @property
    def reason(self) -> str:
        """대기 행을 세우지 못한 도구가 모델에게 대는 사유다."""
        return self.unavailable or f"the confirmation API answered {self.status_code}"


class ChatWriteClient:
    """한 사용자의 한 스레드에만 대기 행을 세우도록 생성 시점에 범위가 묶인 HTTP 확인 창구다."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        user_id: str,
        thread_id: str,
        scope_token: str | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_id = user_id
        self._thread_id = thread_id
        self._scope_token = scope_token

    async def propose(self, tool_name: str, args: JsonObject) -> ChatProposalResult:
        """쓰기 도구 호출 하나를 실행하지 않고 확인 대기 행으로 세운다."""
        if tool_surface(tool_name) != CONFIRM_SURFACE:
            raise ValueError(f"{tool_name} is not a write tool")
        path = CONFIRMATIONS_PATH.replace("{threadId}", self._thread_id)
        response = await self._client.post(
            f"{self._base_url}{path}",
            json={"toolName": tool_name, "args": args},
            headers=scoped_headers(self._user_id, self._scope_token),
        )
        if response.status_code >= 400:
            return ChatProposalResult(
                ok=False, status_code=response.status_code, text=response.text, confirmation_id=""
            )
        text = unwrapped_body(response.text)
        return ChatProposalResult(
            ok=True,
            status_code=response.status_code,
            text=text,
            confirmation_id=_confirmation_id(text),
        )


def _confirmation_id(text: str) -> str:
    try:
        payload = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("confirmationId")
    return value if isinstance(value, str) else ""
