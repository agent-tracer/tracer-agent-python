"""이번 시도가 쓸 모델과 자격과 단가와 한도를 잡 종류와 사용자로 받아 실행 봉투로 옮긴다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from temporalio.exceptions import ApplicationError

# 배포 단위 사이에서만 오가는 창구이며 edge가 바깥에 열지 않는다.
ENVELOPE_PATH = "/internal/jobs/{kind}/envelope"
ENVELOPE_TIMEOUT_S = 20.0
ENVELOPE_UNAVAILABLE = "job.envelope-unavailable"


@dataclass(frozen=True)
class JobExecutionEnvelope:
    """실행 시도 하나가 쓸 모델과 자격과 단가와 한도다."""

    model: str
    fallback_model: str | None
    api_key: str
    model_rates: dict[str, Any]
    limits: dict[str, Any]
    deadline_ms: int


class JobEnvelopeSource(Protocol):
    """실행 시도 하나가 쓸 봉투를 내주는 창구다."""

    async def issue(self, kind: str, user_id: str) -> JobExecutionEnvelope:
        """이 잡 종류와 사용자가 쓸 모델과 자격과 단가와 한도를 실행 봉투로 낸다."""
        ...


class JobEnvelopeClient:
    """잡 종류와 사용자로 실행 시도 하나가 쓸 봉투를 만들어 주는 agent-api 창구다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def issue(self, kind: str, user_id: str) -> JobExecutionEnvelope:
        """이 잡 종류와 사용자가 쓸 모델과 자격과 단가와 한도를 받아 실행 봉투로 낸다."""
        path = ENVELOPE_PATH.format(kind=kind)
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", json={"userId": user_id}, timeout=ENVELOPE_TIMEOUT_S
            )
        except httpx.HTTPError as unreachable:
            raise ApplicationError(
                f"job envelope unreachable: {unreachable}", type=ENVELOPE_UNAVAILABLE
            ) from unreachable
        if response.status_code >= 400:
            raise ApplicationError(
                f"job envelope HTTP {response.status_code}: {response.text[:500]}",
                type=ENVELOPE_UNAVAILABLE,
                # 같은 실행으로 다시 물어도 같은 답이 오는 거절이라 다시 태우지 않는다.
                non_retryable=response.status_code < 500,
            )
        return _envelope(_data(response))


def _data(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as malformed:
        raise ApplicationError(
            "job envelope is not JSON", type=ENVELOPE_UNAVAILABLE, non_retryable=True
        ) from malformed
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise ApplicationError("job envelope has no data", type=ENVELOPE_UNAVAILABLE, non_retryable=True)
    return data


def _envelope(data: dict[str, Any]) -> JobExecutionEnvelope:
    model = data.get("model")
    fallback_model = data.get("fallbackModel")
    api_key = data.get("apiKey")
    model_rates = data.get("modelRates")
    limits = data.get("limits")
    deadline_ms = data.get("deadlineMs")
    if (
        not isinstance(model, str)
        or not isinstance(api_key, str)
        or not isinstance(model_rates, dict)
        or not isinstance(limits, dict)
        or not isinstance(deadline_ms, int)
        or not (fallback_model is None or isinstance(fallback_model, str))
    ):
        raise ApplicationError("job envelope is malformed", type=ENVELOPE_UNAVAILABLE, non_retryable=True)
    return JobExecutionEnvelope(
        model=model,
        fallback_model=fallback_model,
        api_key=api_key,
        model_rates=model_rates,
        limits=limits,
        deadline_ms=deadline_ms,
    )
