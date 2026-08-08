"""워커가 추적 API의 공개 창구를 사용자 범위로 부르는 HTTP 진입점을 소유한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from tracer_agent.shared.agents.shared.json_view import JsonValue
from tracer_agent.shared.agents.shared.wire import MalformedEnvelope, unwrap_envelope

USER_HEADER = "x-monitor-user"
_CLIENT_ERROR = 400
_NOT_FOUND = 404
_SERVER_ERROR = 500


class TracerApiUnavailable(Exception):
    """창구에 닿지 못했거나 창구가 자기 오류를 냈으며 다시 부르면 풀릴 수 있다."""


class TracerApiRejected(Exception):
    """창구가 요청을 거절했으며 같은 본문을 다시 보내도 같은 답이 온다."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"tracer api rejected the request with {status_code}: {body[:500]}")
        self.status_code = status_code


# 창구에 닿지 못한 것만 일시적이며 창구가 거절한 요청은 재시도하지 않는다.
TRANSIENT_TRACER_ERRORS: tuple[type[Exception], ...] = (
    TracerApiUnavailable,
    ConnectionError,
    TimeoutError,
)


class TracerApiPort(Protocol):
    """한 사용자 범위로 추적 창구를 읽고 쓰는 자리이며 대역이 이 모양을 지킨다."""

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> JsonValue:
        """창구 하나를 읽어 봉투를 벗긴 본문을 낸다."""
        ...

    async def post(self, path: str, body: Mapping[str, Any]) -> JsonValue:
        """창구 하나에 본문을 보내고 봉투를 벗긴 응답을 낸다."""
        ...


class TracerApiClient(TracerApiPort):
    """한 사용자의 추적 창구만 부르도록 생성 시점에 범위가 묶인 HTTP 진입점이다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, user_id: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_id = user_id

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> JsonValue:
        """창구 하나를 읽어 봉투를 벗긴 본문을 내며, 이 사용자의 것이 아니면 None을 낸다."""
        response = await self._send("GET", path, params=_query(params))
        if response.status_code == _NOT_FOUND:
            return None
        return _unwrap(self._checked(response))

    async def post(self, path: str, body: Mapping[str, Any]) -> JsonValue:
        """창구 하나에 본문을 보내고 봉투를 벗긴 응답을 낸다."""
        response = await self._send("POST", path, json=dict(body))
        return _unwrap(self._checked(response))

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(
                method, f"{self._base_url}{path}", headers={USER_HEADER: self._user_id}, **kwargs
            )
        except httpx.HTTPError as unreachable:
            raise TracerApiUnavailable(f"tracer api {method} {path} failed: {unreachable}") from unreachable

    @staticmethod
    def _checked(response: httpx.Response) -> httpx.Response:
        if response.status_code >= _SERVER_ERROR:
            raise TracerApiUnavailable(f"tracer api answered {response.status_code}: {response.text[:500]}")
        if response.status_code >= _CLIENT_ERROR:
            raise TracerApiRejected(response.status_code, response.text)
        return response


def _query(params: Mapping[str, Any] | None) -> dict[str, str]:
    if params is None:
        return {}
    return {key: str(value) for key, value in params.items() if value is not None}


def _unwrap(response: httpx.Response) -> JsonValue:
    """계약이 정한 성공 봉투에서 실은 것을 꺼내며 봉투가 아니면 이 창구의 어휘로 옮긴다."""
    try:
        payload = response.json()
    except ValueError as malformed:
        raise TracerApiUnavailable(f"tracer api answered with a non-JSON body: {malformed}") from malformed
    try:
        data = unwrap_envelope(payload)
    except MalformedEnvelope as malformed:
        raise TracerApiUnavailable(
            f"tracer api answered outside the success envelope: {malformed}"
        ) from malformed
    # 이 창구의 없음은 404가 갖는 뜻이므로 실은 칸이 아예 없는 답을 없음으로 읽지 않는다.
    if isinstance(payload, dict) and "data" not in payload:
        raise TracerApiUnavailable("tracer api answered a success envelope that carries no data")
    return data
