"""title-suggestion 도구가 호출마다 실려 받는 요청별 조회를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

from ...runtime.llm.standard_agent import StandardAgentContext
from ..reader import TitleLedgerReader


@dataclass(kw_only=True)
class TitleToolContext(StandardAgentContext):
    """한 모델 호출이 연 도구가 함께 보는 사용자 범위 조회 진입점이다."""

    tool_owner: str
    reader: TitleLedgerReader
