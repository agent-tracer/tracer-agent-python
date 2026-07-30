"""승인된 쓰기 도구를 계약이 선언한 추적 API 자리로 부르고 그 응답에서 문장을 만든다."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from ..intake.router import MONITOR_USER_HEADER
from ..tools.bindings import TOOL_BINDINGS, fill_path
from .tool_calls import plan_chat_tool_call

TOOL_CALL_TIMEOUT_S = 20.0


class ChatToolExecutor(Protocol):
    """승인된 도구 하나를 실제로 부르고 대화에 남길 문장을 낸다."""

    async def execute(self, user_id: str, tool_name: str, args: dict[str, Any]) -> str:
        """도구 하나를 부르고 그 결과를 한 문장으로 낸다."""
        ...


class ChatToolFailed(RuntimeError):
    """승인된 도구 호출이 상류에서 거절되어 대기 행을 닫지 못한다."""


class HttpChatToolExecutor:
    """계약이 선언한 자리로 도구를 부르는 HTTP 실행기다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def execute(self, user_id: str, tool_name: str, args: dict[str, Any]) -> str:
        """승인된 도구 하나를 부르고 그 응답에서 대화에 남길 문장을 만든다."""
        binding = TOOL_BINDINGS[tool_name]
        call = plan_chat_tool_call(tool_name, args)
        body = {key: value for key, value in call.args.items() if key not in binding.path_args}
        body.update(binding.body_constants)
        response = await self._client.request(
            binding.method,
            f"{self._base_url}{fill_path(binding, call.args)}",
            json=body,
            headers={MONITOR_USER_HEADER: user_id},
            timeout=TOOL_CALL_TIMEOUT_S,
        )
        if response.status_code >= 400:
            raise ChatToolFailed(f"{tool_name} answered {response.status_code}")
        return call.describe(_data(response.text))


def _data(raw: str) -> Any:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("ok") is True:
        return payload.get("data")
    return payload
