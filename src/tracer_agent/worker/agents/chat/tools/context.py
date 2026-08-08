"""chat 도구가 호출마다 실려 받는 요청별 협력자와 그 턴이 세운 확인 대기 행 장부를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

from tracer_agent.shared.agents.chat.models import ProposedWrite

from ...runtime.llm.standard_agent import StandardAgentContext
from ..backends import ChatTurnBackends


@dataclass(kw_only=True)
class ChatToolContext(StandardAgentContext):
    """한 턴이 연 도구가 함께 보는 진입점과 그 턴이 세운 대기 행 장부다."""

    tool_owner: str
    backends: ChatTurnBackends
    proposals: list[ProposedWrite]
