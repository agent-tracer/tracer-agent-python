"""이번 시도가 쓸 모델과 자격과 단가와 한도를 잡 종류와 사용자로 받아 실행 봉투로 옮긴다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from ..agents.shared.json_view import JsonObject
from .envelope_client import ENVELOPE_TIMEOUT_S, InternalEnvelopeClient

__all__ = [
    "ENVELOPE_PATH",
    "ENVELOPE_TIMEOUT_S",
    "ENVELOPE_UNAVAILABLE",
    "JobEnvelopeClient",
    "JobEnvelopeSource",
    "JobExecutionEnvelope",
]

# 배포 단위 사이에서만 오가는 창구이며 edge가 바깥에 열지 않는다.
ENVELOPE_PATH = "/internal/jobs/{kind}/envelope"
ENVELOPE_UNAVAILABLE = "job.envelope-unavailable"


@dataclass(frozen=True)
class JobExecutionEnvelope:
    """실행 시도 하나가 쓸 모델과 자격과 단가와 한도다."""

    model: str
    fallback_model: str | None
    api_key: str
    model_rates: JsonObject
    limits: JsonObject
    deadline_ms: int


class JobEnvelopeSource(Protocol):
    """실행 시도 하나가 쓸 봉투를 내주는 창구다."""

    async def issue(self, kind: str, user_id: str) -> JobExecutionEnvelope:
        """이 잡 종류와 사용자가 쓸 모델과 자격과 단가와 한도를 실행 봉투로 낸다."""
        ...


class JobEnvelopeClient(InternalEnvelopeClient):
    """잡 종류와 사용자로 실행 시도 하나가 쓸 봉투를 만들어 주는 agent-api 창구다."""

    error_type: ClassVar[str] = ENVELOPE_UNAVAILABLE
    label: ClassVar[str] = "job envelope"

    async def issue(self, kind: str, user_id: str) -> JobExecutionEnvelope:
        """이 잡 종류와 사용자가 쓸 모델과 자격과 한도를 받아 실행 봉투로 낸다."""
        data = await self._post(ENVELOPE_PATH.format(kind=kind), {"userId": user_id})
        return self._envelope(data)

    def _envelope(self, data: JsonObject) -> JobExecutionEnvelope:
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
            raise self._failed("is malformed", final=True)
        return JobExecutionEnvelope(
            model=model,
            fallback_model=fallback_model,
            api_key=api_key,
            model_rates=model_rates,
            limits=limits,
            deadline_ms=deadline_ms,
        )
