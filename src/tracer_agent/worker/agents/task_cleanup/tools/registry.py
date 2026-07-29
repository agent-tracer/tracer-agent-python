"""요청별 의존을 받아 task-cleanup 도구 레지스트리를 조립하고 인자를 검증한다."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch, CleanupCandidate

from ...runtime.tooling import ToolRegistry
from ..reader import CleanupLedgerReader
from .get_events import GET_TASK_EVENTS, GetTaskEventsTool
from .list_candidates import LIST_CANDIDATE_TASKS, ListCandidateTasksTool

TRIAGE_TOOL_NAMES: tuple[str, ...] = (LIST_CANDIDATE_TASKS,)
INSPECT_TOOL_NAMES: tuple[str, ...] = (GET_TASK_EVENTS,)
# 조율자는 후보를 직접 열어보지 않고 검토 전문가의 보고만으로 제안을 쓴다.
COORDINATOR_TOOL_NAMES: tuple[str, ...] = ()

_ARGS_BY_TOOL: dict[str, type[BaseModel]] = {
    cls.name: cls.args_model for cls in (ListCandidateTasksTool, GetTaskEventsTool)
}


def validate_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """모델이 고른 도구 인자를 소유 스키마로 검증해 조회 인자를 만든다."""
    args_model = _ARGS_BY_TOOL.get(name)
    if args_model is None:
        raise ValueError(f"unknown task-cleanup tool: {name}")
    return args_model.model_validate(args).model_dump(exclude_none=True)


def build_cleanup_registry(
    reader: CleanupLedgerReader,
    batch: CleanupBatch,
    exposed_candidates: dict[str, CleanupCandidate],
    event_ids_by_task: dict[str, set[str]],
    *,
    agent_name: str,
) -> ToolRegistry:
    """요청별 원장 조회와 후보 배치와 근거 장부를 쥔 도구 레지스트리를 만든다."""
    return ToolRegistry(
        (
            ListCandidateTasksTool(batch, exposed_candidates),
            GetTaskEventsTool(reader, event_ids_by_task),
        ),
        agent_name=agent_name,
    )
