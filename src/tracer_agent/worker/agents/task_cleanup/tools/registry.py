"""task-cleanup 도구 하나의 목록과 그 목록으로 세운 레지스트리를 소유한다."""

from __future__ import annotations

from typing import Any

from ...runtime.tooling import AgentTool, ToolRegistry
from .context import CleanupToolContext
from .get_events import GET_TASK_EVENTS, GetTaskEventsTool

CLEANUP_TOOL_CLASSES: tuple[type[AgentTool[Any, CleanupToolContext]], ...] = (GetTaskEventsTool,)

# 조율자는 후보를 되읽지 않고 요청이 실어 준 배치만 본다.
TRIAGE_TOOL_NAMES: tuple[str, ...] = ()
INSPECT_TOOL_NAMES: tuple[str, ...] = (GET_TASK_EVENTS,)
# 조율자는 후보를 직접 조회하지 않고 검토 전문가의 보고만으로 제안을 쓴다.
COORDINATOR_TOOL_NAMES: tuple[str, ...] = ()

# 도구가 요청별 조회와 장부를 호출 컨텍스트로 받으므로 레지스트리 하나가 모든 실행을 함께 쓴다.
CLEANUP_TOOLS: ToolRegistry[CleanupToolContext] = ToolRegistry(tuple(cls() for cls in CLEANUP_TOOL_CLASSES))
