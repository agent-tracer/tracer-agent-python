"""태스크 이벤트 페이지를 사용자 범위 추적 창구로 읽고 조회한 이벤트를 근거로 올린다."""

from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tracer_agent.shared.agents.shared.models import TrimmedStr
from tracer_agent.shared.agents.task_cleanup.models import EventPage

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from ...shared.contract_prompt_source import ContractPromptSource
from .context import CleanupToolContext

GET_TASK_EVENTS = "get_task_events"
_CONTRACT = ContractPromptSource()
_TOOL = _CONTRACT.tool("task-cleanup", GET_TASK_EVENTS)
_ARGS = _TOOL["args"]
_LIMIT = _ARGS["limit"]
DEFAULT_EVENT_LIMIT = int(_LIMIT["default"])
MIN_EVENT_LIMIT = int(_LIMIT["min"])
MAX_EVENT_LIMIT = int(_LIMIT["max"])

EventOrder = Literal["asc", "desc"]
DEFAULT_EVENT_ORDER = cast(EventOrder, _ARGS["order"]["default"])


class GetTaskEventsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1, description=str(_ARGS["taskId"]["description"]))
    limit: int = Field(
        default=DEFAULT_EVENT_LIMIT,
        ge=MIN_EVENT_LIMIT,
        le=MAX_EVENT_LIMIT,
        description=str(_LIMIT["description"]),
    )
    cursor: TrimmedStr | None = Field(
        default=None,
        min_length=1,
        description=str(_ARGS["cursor"]["description"]),
    )
    order: EventOrder = Field(
        default=DEFAULT_EVENT_ORDER,
        description=str(_ARGS["order"]["description"]),
    )


GET_TASK_EVENTS_DESCRIPTION = str(_TOOL["description"])


class GetTaskEventsTool(AgentTool[GetTaskEventsArgs, CleanupToolContext]):
    """태스크 이벤트를 사용자 범위로 읽고 읽은 이벤트 id만 근거로 올린다."""

    name = GET_TASK_EVENTS
    description = GET_TASK_EVENTS_DESCRIPTION
    args_model = GetTaskEventsArgs
    transient_errors = TRANSIENT_TRACER_ERRORS

    async def execute(self, args: GetTaskEventsArgs, context: CleanupToolContext) -> str:
        # 계약의 batchScope 는 이 실행이 볼 자리를 후보 배치로 좁히므로 사용자 범위 안이라도 밖은 읽지 않는다.
        if args.taskId not in {candidate.id for candidate in context.batch.candidates}:
            return f"Task {args.taskId} not found."
        events = await context.reader.task_events(
            args.taskId,
            args.limit,
            args.cursor,
            args.order,
        )
        if events is None:
            return f"Task {args.taskId} not found."
        return json.dumps(events, ensure_ascii=False)

    def record(self, args: GetTaskEventsArgs, content: str, context: CleanupToolContext, /) -> None:
        try:
            page = EventPage.model_validate_json(content)
        except ValidationError:
            return
        known = context.event_ids_by_task.setdefault(args.taskId, set())
        for event in page.events:
            known.add(event.id)
