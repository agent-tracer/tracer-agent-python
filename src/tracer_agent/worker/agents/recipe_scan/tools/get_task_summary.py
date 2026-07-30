"""태스크 요약을 저비용으로 읽는 도구의 이름·스키마·설명·실행을 소유한다."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from ..reader import RecipeLedgerReader
from ..summary import build_task_summary

GET_TASK_SUMMARY = "get_task_summary"
DEFAULT_SUMMARY_WINDOW = 400
MAX_SUMMARY_WINDOW = 2_000


class GetTaskSummaryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1, description="The task ID")
    window: int = Field(
        default=DEFAULT_SUMMARY_WINDOW,
        ge=1,
        le=MAX_SUMMARY_WINDOW,
        description=(
            f"How many of the task's earliest events to aggregate "
            f"(default {DEFAULT_SUMMARY_WINDOW}, hard cap {MAX_SUMMARY_WINDOW})"
        ),
    )


GET_TASK_SUMMARY_DESCRIPTION = (
    "Get a cheap task overview (tool usage counts, top files touched, top commands run, first "
    "user message) aggregated over the task's earliest events, window many, default "
    f"{DEFAULT_SUMMARY_WINDOW}. The response's truncated/totalEventCount fields tell you whether "
    "later events were left out."
)


class GetTaskSummaryTool(AgentTool[GetTaskSummaryArgs]):
    """앵커 태스크의 저비용 요약을 추적 창구에서 읽는다."""

    name = GET_TASK_SUMMARY
    description = GET_TASK_SUMMARY_DESCRIPTION
    args_model = GetTaskSummaryArgs
    # 창구에 닿지 못한 것만 일시적이며 창구가 거절한 요청은 재시도하지 않는다.
    transient_errors = TRANSIENT_TRACER_ERRORS

    def __init__(self, reader: RecipeLedgerReader) -> None:
        self._reader = reader

    async def execute(self, args: GetTaskSummaryArgs) -> str:
        loaded = await self._reader.task_with_events(args.taskId, args.window)
        if loaded is None:
            return f"Task {args.taskId} not found."
        summary = build_task_summary(loaded["task"], loaded["rows"], loaded["total"])
        return json.dumps(summary, ensure_ascii=False)
