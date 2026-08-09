"""이번 시도가 쓸 단가와 한도와 자격을 받아 실행 봉투로 옮긴다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from ...shared.agents.shared.json_view import JsonObject
from ...shared.workflows.envelope_client import ENVELOPE_TIMEOUT_S, InternalEnvelopeClient

__all__ = [
    "ENVELOPE_PATH",
    "ENVELOPE_TIMEOUT_S",
    "ENVELOPE_UNAVAILABLE",
    "ChatEnvelopeClient",
    "ChatEnvelopeSource",
    "ChatExecutionEnvelope",
]

# 배포 단위 사이에서만 오가는 창구이며 edge가 바깥에 열지 않는다.
ENVELOPE_PATH = "/internal/chat/executions/{execution_id}/envelope"
ENVELOPE_UNAVAILABLE = "chat.envelope-unavailable"


@dataclass(frozen=True)
class ChatExecutionEnvelope:
    """실행 봉투로 실릴 값과, 원장이 draft 창구를 알아보게 할 지문이다."""

    fields: dict[str, Any]


class ChatEnvelopeSource(Protocol):
    """실행 시도 하나가 쓸 봉투를 내주는 창구다."""

    async def issue(self, execution_id: str, attempt: int) -> ChatExecutionEnvelope:
        """이 시도가 쓸 단가와 한도와 자격을 실행 봉투 조각으로 낸다."""
        ...


class ChatEnvelopeClient(InternalEnvelopeClient):
    """실행 시도 하나가 쓸 봉투를 만들어 주는 agent-api 창구다."""

    error_type: ClassVar[str] = ENVELOPE_UNAVAILABLE
    label: ClassVar[str] = "chat envelope"

    async def issue(self, execution_id: str, attempt: int) -> ChatExecutionEnvelope:
        """이 시도가 쓸 단가와 한도와 자격을 받아 실행 봉투 조각으로 낸다."""
        data = await self._post(ENVELOPE_PATH.format(execution_id=execution_id))
        return self._envelope(data, attempt)

    def _envelope(self, data: JsonObject, attempt: int) -> ChatExecutionEnvelope:
        fields = dict(data)
        # 되읽기와 확인 창구는 에이전트의 것이므로 실행기가 자기 배포 단위의 주소로 그것을 부른다.
        fields["agentApiBaseUrl"] = self._base_url
        fields["attempt"] = attempt
        return ChatExecutionEnvelope(fields=fields)
