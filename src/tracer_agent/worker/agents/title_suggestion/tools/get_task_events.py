"""태스크 이벤트 한 페이지를 사용자 범위 원장 뷰로 읽는 도구를 소유한다."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ..reader import TitleLedgerReader

GET_TASK_EVENTS = "get_task_events"
DEFAULT_EVENT_LIMIT = 100
MIN_EVENT_LIMIT = 1
MAX_EVENT_LIMIT = 300

EventOrder = Literal["asc", "desc"]
DEFAULT_EVENT_ORDER: EventOrder = "asc"


class GetTaskEventsArgs(BaseModel):
    """태스크 이벤트 페이지 조회 인자를 계약대로 검증한다."""

    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1, description="The task ID")
    limit: int = Field(
        default=DEFAULT_EVENT_LIMIT,
        ge=MIN_EVENT_LIMIT,
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
    order: EventOrder = Field(
        default=DEFAULT_EVENT_ORDER,
        description=(
            'Reading direction: "asc" (default) pages from the earliest event forward; '
            '"desc" pages from the latest event backward.'
        ),
    )


GET_TASK_EVENTS_DESCRIPTION = (
    "Get a page of a task's chronological event sequence (user messages, assistant messages, tool "
    f"runs), up to {MAX_EVENT_LIMIT} events per page. You choose how much to read: pick limit, pass "
    'the response\'s nextCursor back as cursor to keep paging, and set order="desc" to start from '
    "the latest events (e.g. to see how a long task ended). truncated/total tell you whether more "
    "events exist. Call this when the conversation excerpt in the prompt is too thin to name the work."
)


class GetTaskEventsTool(AgentTool[GetTaskEventsArgs]):
    """태스크 이벤트를 사용자 범위로 읽어 대화 발췌만으로 부족한 근거를 채운다."""

    name = GET_TASK_EVENTS
    description = GET_TASK_EVENTS_DESCRIPTION
    args_model = GetTaskEventsArgs

    def __init__(self, reader: TitleLedgerReader) -> None:
        self._reader = reader

    async def execute(self, args: GetTaskEventsArgs) -> str:
        page = await self._reader.task_events(args.taskId, args.limit, args.cursor, args.order)
        if page is None:
            return f"Task {args.taskId} not found."
        return json.dumps(page, ensure_ascii=False)
