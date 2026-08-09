"""태스크 이벤트 한 페이지를 읽고 조회한 이벤트를 근거로 올리는 도구를 소유한다."""

from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from ...shared.contract_prompt_source import ContractPromptSource
from .context import RecipeToolContext
from .provenance import add_events, loaded

GET_TASK_EVENTS = "get_task_events"
_CONTRACT = ContractPromptSource()
_TOOL = _CONTRACT.tool("recipe-scan", GET_TASK_EVENTS)
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


class GetTaskEventsTool(AgentTool[GetTaskEventsArgs, RecipeToolContext]):
    """앵커 태스크의 원본 이벤트를 사용자 범위로 읽고 조회한 이벤트를 근거로 올린다."""

    name = GET_TASK_EVENTS
    description = GET_TASK_EVENTS_DESCRIPTION
    args_model = GetTaskEventsArgs
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
