"""배포 단위 사이에서만 오가는 봉투 창구의 왕복과 봉투 해체와 오류 분류를 소유한다."""

from __future__ import annotations

from typing import ClassVar

import httpx
from temporalio.exceptions import ApplicationError

from ..agents.shared.json_view import JsonObject
from ..agents.shared.wire import MalformedEnvelope, unwrap_envelope

ENVELOPE_TIMEOUT_S = 20.0


class InternalEnvelopeClient:
    """봉투 창구 하나를 부르고 실은 것만 내며 서브클래스가 경로와 결과 매핑을 갖는다."""

    # 이 창구의 실패를 워크플로가 알아보는 이름이며 서브클래스가 정한다.
    error_type: ClassVar[str]
    # 실패 문구가 어느 창구의 것인지 알리는 이름이며 서브클래스가 정한다.
    label: ClassVar[str]

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def _post(self, path: str, body: JsonObject | None = None) -> JsonObject:
        """창구 하나를 부르고 계약이 정한 봉투에서 실은 객체를 꺼낸다."""
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", json=body, timeout=ENVELOPE_TIMEOUT_S
            )
        except httpx.HTTPError as unreachable:
            raise self._failed(f"unreachable: {unreachable}") from unreachable
        if response.status_code >= 400:
            raise ApplicationError(
                f"{self.label} HTTP {response.status_code}: {response.text[:500]}",
                type=self.error_type,
                # 같은 실행으로 다시 물어도 같은 답이 오는 거절이라 다시 실행하지 않는다.
                non_retryable=response.status_code < 500,
            )
        return self._data(response)

    def _data(self, response: httpx.Response) -> JsonObject:
        try:
            payload = response.json()
        except ValueError as malformed:
            raise self._failed("is not JSON", final=True) from malformed
        try:
            data = unwrap_envelope(payload)
        except MalformedEnvelope as malformed:
            raise self._failed("has no data", final=True) from malformed
        if not isinstance(data, dict):
            raise self._failed("has no data", final=True)
        return data

    def _failed(self, reason: str, *, final: bool = False) -> ApplicationError:
        """이 창구의 실패를 워크플로가 읽는 어휘로 옮긴다."""
        return ApplicationError(f"{self.label} {reason}", type=self.error_type, non_retryable=final)
