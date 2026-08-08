"""승인된 쓰기 도구를 계약이 선언한 추적 API 자리로 부르고 그 응답에서 문장을 만든다."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from ...runtime.dependencies import MONITOR_USER_HEADER
from ...shared.wire import MalformedEnvelope, unwrap_envelope
from ..tools.bindings import binding_for, fill_path
from .tool_calls import plan_chat_tool_call

TOOL_CALL_TIMEOUT_S = 20.0


class ChatToolExecutor(Protocol):
    """승인된 도구 하나를 실제로 부르고 대화에 남길 문장을 낸다."""

    async def execute(self, user_id: str, tool_name: str, args: dict[str, Any]) -> str:
        """도구 하나를 부르고 그 결과를 한 문장으로 낸다."""
        ...


class ChatToolFailed(RuntimeError):
    """승인된 도구 호출이 상류에서 거절되어 대기 행을 닫지 못하며 그 사유를 함께 싣는다."""

    def __init__(self, message: str, *, status: int, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.details = details


def _calls_agent_upstream(path: str) -> bool:
    """경로는 도구의 표면을 정하지 않고 그 호출이 어느 상류로 나가는지만 정한다."""
    return path.startswith("/api/agent/")


class HttpChatToolExecutor:
    """계약이 선언한 자리로 도구를 부르는 HTTP 실행기다."""

    def __init__(self, client: httpx.AsyncClient, tracer_base_url: str, agent_base_url: str) -> None:
        self._client = client
        self._tracer_base_url = tracer_base_url.rstrip("/")
        self._agent_base_url = agent_base_url.rstrip("/")

    async def execute(self, user_id: str, tool_name: str, args: dict[str, Any]) -> str:
        """승인된 도구 하나를 부르고 그 응답에서 대화에 남길 문장을 만든다."""
        call = plan_chat_tool_call(tool_name, args)
        binding = binding_for(tool_name, call.args)
        body = {
            key: value for key, value in call.args.items() if key not in binding.path_args and key != "action"
        }
        body.update(binding.body_constants)
        base_url = self._agent_base_url if _calls_agent_upstream(binding.path) else self._tracer_base_url
        response = await self._client.request(
            binding.method,
            f"{base_url}{fill_path(binding, call.args)}",
            json=body,
            headers={MONITOR_USER_HEADER: user_id},
            timeout=TOOL_CALL_TIMEOUT_S,
        )
        if response.status_code >= 400:
            raise ChatToolFailed(
                f"{tool_name} answered {response.status_code}",
                status=response.status_code,
                details=_rejection_details(response.text),
            )
        return call.describe(_data(response.text))


def _rejection_details(raw: str) -> Any:
    """상류가 계약의 오류 봉투로 적어 보낸 거절 사유만 꺼낸다."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error.get("details") if isinstance(error, dict) else None


def _data(raw: str) -> Any:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    try:
        return unwrap_envelope(payload)
    except MalformedEnvelope:
        # 계약 밖의 상류도 문장을 만들 수 있도록 봉투가 아니면 실린 것 그대로 읽는다.
        return payload
