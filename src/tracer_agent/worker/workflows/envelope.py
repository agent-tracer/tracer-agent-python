"""이번 시도가 쓸 단가와 한도와 자격을 받아 실행 봉투로 옮긴다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from temporalio.exceptions import ApplicationError

# 배포 단위 사이에서만 오가는 창구이며 edge가 바깥에 열지 않는다.
ENVELOPE_PATH = "/internal/chat/executions/{execution_id}/envelope"
ENVELOPE_TIMEOUT_S = 20.0
ENVELOPE_UNAVAILABLE = "chat.envelope-unavailable"


@dataclass(frozen=True)
class ChatExecutionEnvelope:
    """실행 봉투로 실릴 값과, 원장이 draft 창구를 알아보게 할 지문이다."""

    fields: dict[str, Any]
    draft_token_hash: str


class ChatEnvelopeSource(Protocol):
    """실행 시도 하나가 쓸 봉투를 내주는 창구다."""

    async def issue(self, execution_id: str, attempt: int) -> ChatExecutionEnvelope:
        """이 시도가 쓸 단가와 한도와 자격을 실행 봉투 조각으로 낸다."""
        ...


class ChatEnvelopeClient:
    """실행 시도 하나가 쓸 봉투를 만들어 주는 agent-api 창구다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def issue(self, execution_id: str, attempt: int) -> ChatExecutionEnvelope:
        """이 시도가 쓸 단가와 한도와 자격을 받아 실행 봉투 조각으로 낸다."""
        path = ENVELOPE_PATH.format(execution_id=execution_id)
        try:
            response = await self._client.post(f"{self._base_url}{path}", timeout=ENVELOPE_TIMEOUT_S)
        except httpx.HTTPError as unreachable:
            raise ApplicationError(
                f"chat envelope unreachable: {unreachable}", type=ENVELOPE_UNAVAILABLE
            ) from unreachable
        if response.status_code >= 400:
            raise ApplicationError(
                f"chat envelope HTTP {response.status_code}: {response.text[:500]}",
                type=ENVELOPE_UNAVAILABLE,
                # 같은 실행으로 다시 물어도 같은 답이 오는 거절이라 다시 태우지 않는다.
                non_retryable=response.status_code < 500,
            )
        return _envelope(_data(response), attempt)


def _data(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as malformed:
        raise ApplicationError(
            "chat envelope is not JSON", type=ENVELOPE_UNAVAILABLE, non_retryable=True
        ) from malformed
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise ApplicationError("chat envelope has no data", type=ENVELOPE_UNAVAILABLE, non_retryable=True)
    return data


def _envelope(data: dict[str, Any], attempt: int) -> ChatExecutionEnvelope:
    draft = data.get("draft")
    if not isinstance(draft, dict):
        raise ApplicationError(
            "chat envelope has no draft grant", type=ENVELOPE_UNAVAILABLE, non_retryable=True
        )
    fields = {key: value for key, value in data.items() if key != "draft"}
    fields["draftCallback"] = {"url": draft["url"], "token": draft["token"], "attempt": attempt}
    return ChatExecutionEnvelope(fields=fields, draft_token_hash=str(draft["tokenHash"]))
