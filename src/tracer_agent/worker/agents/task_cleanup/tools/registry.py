"""task-cleanup 도구 레지스트리 하나를 소유하고 모델이 고른 인자를 검증한다."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ...runtime.tooling import ToolRegistry
from .context import CleanupToolContext
from .get_events import GET_TASK_EVENTS, GetTaskEventsTool

# 조율자는 후보를 되읽지 않고 요청이 실어 준 배치만 본다.
TRIAGE_TOOL_NAMES: tuple[str, ...] = ()
INSPECT_TOOL_NAMES: tuple[str, ...] = (GET_TASK_EVENTS,)
# 조율자는 후보를 직접 조회하지 않고 검토 전문가의 보고만으로 제안을 쓴다.
COORDINATOR_TOOL_NAMES: tuple[str, ...] = ()

_ARGS_BY_TOOL: dict[str, type[BaseModel]] = {cls.name: cls.args_model for cls in (GetTaskEventsTool,)}


def validate_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """모델이 고른 도구 인자를 소유 스키마로 검증해 조회 인자를 만든다."""
    args_model = _ARGS_BY_TOOL.get(name)
    if args_model is None:
        raise ValueError(f"unknown task-cleanup tool: {name}")
    return args_model.model_validate(args).model_dump(exclude_none=True)


# 도구가 요청별 조회와 장부를 호출 컨텍스트로 받으므로 레지스트리 하나가 모든 실행을 함께 쓴다.
CLEANUP_TOOLS: ToolRegistry[CleanupToolContext] = ToolRegistry((GetTaskEventsTool(),))
