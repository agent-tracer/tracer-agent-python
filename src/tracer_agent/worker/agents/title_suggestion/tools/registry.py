"""title-suggestion 도구 레지스트리 하나를 소유한다."""

from __future__ import annotations

from ...runtime.tooling import ToolRegistry
from .context import TitleToolContext
from .get_task_events import GetTaskEventsTool

# 도구가 요청별 조회를 호출 컨텍스트로 받으므로 레지스트리 하나가 모든 실행을 함께 쓴다.
TITLE_TOOLS: ToolRegistry[TitleToolContext] = ToolRegistry((GetTaskEventsTool(),))
