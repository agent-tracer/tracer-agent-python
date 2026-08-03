"""태스크 이벤트 한 페이지를 읽고 조회한 이벤트를 근거로 올리는 도구를 소유한다."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from .context import RecipeToolContext
from .provenance import add_events, loaded

GET_TASK_EVENTS = "get_task_events"
DEFAULT_EVENT_LIMIT = 100
MAX_EVENT_LIMIT = 300


class GetTaskEventsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1, description="The task ID")
    limit: int = Field(
        default=DEFAULT_EVENT_LIMIT,
        ge=1,
        le=MAX_EVENT_LIMIT,
        description=(
            f"Max events to return in this page (default {DEFAULT_EVENT_LIMIT}, hard cap {MAX_EVENT_LIMIT})"
        ),
    )
    cursor: TrimmedStr | None = Field(
        default=None,
        min_length=1,
        description=("Opaque cursor from a previous call's nextCursor. Omit to start from the first page."),
    )
    order: Literal["asc", "desc"] = Field(
        default="asc",
        description=(
            'Reading direction: "asc" (default) pages from the earliest event forward; '
            '"desc" pages from the latest event backward.'
        ),
    )


GET_TASK_EVENTS_DESCRIPTION = (
    "Get a page of a task's chronological event sequence (user messages, assistant messages, tool "
    f"runs), up to {MAX_EVENT_LIMIT} events per page. You choose how much to read: pick limit, pass the "
    'response\'s nextCursor back as cursor to keep paging, and set order="desc" to start from the '
    "latest events. truncated/total tell you whether more events exist."
)


class GetTaskEventsTool(AgentTool[GetTaskEventsArgs, RecipeToolContext]):
    """앵커 태스크의 원본 이벤트를 사용자 범위로 읽고 조회한 이벤트를 근거로 올린다."""

    name = GET_TASK_EVENTS
    description = GET_TASK_EVENTS_DESCRIPTION
    args_model = GetTaskEventsArgs
    # 창구에 닿지 못한 것만 일시적이며 창구가 거절한 요청은 재시도하지 않는다.
    transient_errors = TRANSIENT_TRACER_ERRORS

    async def execute(self, args: GetTaskEventsArgs, context: RecipeToolContext) -> str:
        page = await context.reader.task_events(args.taskId, args.limit, args.cursor, args.order)
        if page is None:
            return f"Task {args.taskId} not found."
        return json.dumps(page, ensure_ascii=False)

    def record(self, args: GetTaskEventsArgs, content: str, context: RecipeToolContext, /) -> None:
        parsed = loaded(content)
        if isinstance(parsed, dict):
            add_events(context.catalog, parsed.get("events"), args.taskId)
