"""태스크 이벤트 페이지를 사용자 범위 추적 창구로 읽고 조회한 이벤트를 근거로 올린다."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tracer_agent.shared.agents.shared.models import TrimmedStr
from tracer_agent.shared.agents.task_cleanup.models import EventPage

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from ..reader import CleanupLedgerReader

GET_TASK_EVENTS = "get_task_events"
DEFAULT_EVENT_LIMIT = 100
MAX_EVENT_LIMIT = 300

EventOrder = Literal["asc", "desc"]
DEFAULT_EVENT_ORDER: EventOrder = "asc"


class GetTaskEventsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1, description="The task ID")
    limit: int | None = Field(
        default=None,
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
    order: EventOrder | None = Field(
        default=None,
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
    "events exist. Call this whenever you need to see what actually happened in a task before "
    "judging it."
)


class GetTaskEventsTool(AgentTool[GetTaskEventsArgs]):
    """태스크 이벤트를 사용자 범위로 읽고 읽은 이벤트 id만 근거로 올린다."""

    name = GET_TASK_EVENTS
    description = GET_TASK_EVENTS_DESCRIPTION
    args_model = GetTaskEventsArgs
    # 창구에 닿지 못한 것만 일시적이며 창구가 거절한 요청은 재시도하지 않는다.
    transient_errors = TRANSIENT_TRACER_ERRORS

    def __init__(self, reader: CleanupLedgerReader, event_ids: dict[str, set[str]]) -> None:
        self._reader = reader
        self._event_ids = event_ids

    async def execute(self, args: GetTaskEventsArgs) -> str:
        events = await self._reader.task_events(
            args.taskId,
            args.limit if args.limit is not None else DEFAULT_EVENT_LIMIT,
            args.cursor,
            args.order if args.order is not None else DEFAULT_EVENT_ORDER,
        )
        if events is None:
            return f"Task {args.taskId} not found."
        return json.dumps(events, ensure_ascii=False)

    def record(self, args: GetTaskEventsArgs, content: str) -> None:
        try:
            page = EventPage.model_validate_json(content)
        except ValidationError:
            return
        known = self._event_ids.setdefault(args.taskId, set())
        for event in page.events:
            known.add(event.id)
