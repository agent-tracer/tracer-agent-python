"""요청별 의존을 받아 title-suggestion 도구 레지스트리를 조립한다."""

from __future__ import annotations

from ...runtime.tooling import ToolRegistry
from ..reader import TitleLedgerReader
from .get_task_events import GetTaskEventsTool


def build_title_registry(reader: TitleLedgerReader, *, agent_name: str) -> ToolRegistry:
    """요청별 사용자 범위 원장 조회를 쥔 도구 레지스트리를 만든다."""
    return ToolRegistry((GetTaskEventsTool(reader),), agent_name=agent_name)
