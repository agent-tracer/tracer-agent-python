"""태스크 이벤트 한 페이지를 사용자 범위 추적 창구로 읽는 도구를 소유한다."""

from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from ...shared.contract_prompt_source import ContractPromptSource
from .context import TitleToolContext

GET_TASK_EVENTS = "get_task_events"
_CONTRACT = ContractPromptSource()
_TOOL = _CONTRACT.tool("title-suggestion", GET_TASK_EVENTS)
_ARGS = _TOOL["args"]
_LIMIT = _ARGS["limit"]
DEFAULT_EVENT_LIMIT = int(_LIMIT["default"])
MIN_EVENT_LIMIT = int(_LIMIT["min"])
MAX_EVENT_LIMIT = int(_LIMIT["max"])

EventOrder = Literal["asc", "desc"]
DEFAULT_EVENT_ORDER = cast(EventOrder, _ARGS["order"]["default"])


class GetTaskEventsArgs(BaseModel):
    """태스크 이벤트 페이지 조회 인자를 계약대로 검증한다."""

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


class GetTaskEventsTool(AgentTool[GetTaskEventsArgs, TitleToolContext]):
    """태스크 이벤트를 사용자 범위로 읽어 대화 발췌만으로 부족한 근거를 채운다."""

    name = GET_TASK_EVENTS
    description = GET_TASK_EVENTS_DESCRIPTION
    args_model = GetTaskEventsArgs
    # 같은 창구를 읽는 recipe·cleanup의 도구와 같은 오류를 일시로 본다.
    transient_errors = TRANSIENT_TRACER_ERRORS

    async def execute(self, args: GetTaskEventsArgs, context: TitleToolContext) -> str:
        page = await context.reader.task_events(args.taskId, args.limit, args.cursor, args.order)
        if page is None:
            return f"Task {args.taskId} not found."
        return json.dumps(page, ensure_ascii=False)
