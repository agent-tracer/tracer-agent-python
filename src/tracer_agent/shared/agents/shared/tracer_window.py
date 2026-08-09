"""창구 프로세스가 추적 API 의 공개 자리를 사용자 범위로 부르는 진입점을 소유한다."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from .json_view import JsonValue

MONITOR_USER_HEADER = "x-monitor-user"

# 상류가 거절조차 내지 못하고 실패했을 때 부르는 쪽이 보는 상태다.
UPSTREAM_FAILURE_STATUS = 502
UPSTREAM_FAILURE_CODE = "tracer_api_failed"
_CLIENT_ERROR = 400
_SERVER_ERROR = 600


class UpstreamRejected(Exception):
    """추적이 낸 거절이며 상태와 코드를 그대로 실어 부르는 쪽이 다시 분류하지 않게 한다."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status if _CLIENT_ERROR <= status < _SERVER_ERROR else UPSTREAM_FAILURE_STATUS
        self.code = code
        self.message = message


class TracerWindow(Protocol):
    """추적 API 한 자리를 사용자 범위로 부르는 창구다."""

    async def request(
        self,
        method: str,
        path: str,
        user_id: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> JsonValue:
        """자리 하나를 부르고 성공이면 봉투를 벗긴 본문을 낸다."""
        ...


class HttpTracerWindow:
    """추적 API 를 HTTP 로 부르며 거절의 상태와 코드를 그대로 올린다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def request(
        self,
        method: str,
        path: str,
        user_id: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> JsonValue:
        """사용자 머리글을 실어 부르고 상류가 낸 거절은 상태와 코드를 그대로 실은 예외로 낸다."""
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers={MONITOR_USER_HEADER: user_id},
                params=_query(query),
                json=body,
            )
        except httpx.HTTPError as unreachable:
            raise UpstreamRejected(
                UPSTREAM_FAILURE_STATUS, UPSTREAM_FAILURE_CODE, f"tracer api {method} {path} failed"
            ) from unreachable
        payload = _payload(response)
        if response.status_code >= _CLIENT_ERROR:
            raise _rejection(response.status_code, payload, response.text)
        return payload.get("data") if isinstance(payload, dict) and payload.get("ok") is True else payload


def _query(query: dict[str, Any] | None) -> dict[str, str]:
    if query is None:
        return {}
    return {name: str(value) for name, value in query.items() if value is not None}


def _payload(response: httpx.Response) -> JsonValue:
    try:
        parsed: JsonValue = response.json()
    except ValueError:
        return None
    return parsed


def _rejection(status: int, payload: JsonValue, fallback: str) -> UpstreamRejected:
    """오류 봉투면 그 코드와 문장을, 아니면 상태만 아는 거절을 만든다."""
    if isinstance(payload, dict) and payload.get("ok") is False:
        error = payload.get("error")
        if isinstance(error, dict):
            return UpstreamRejected(status, str(error.get("code")), str(error.get("message")))
    return UpstreamRejected(status, UPSTREAM_FAILURE_CODE, fallback[:500])
