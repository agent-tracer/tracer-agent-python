"""이벤트를 검색하고 태스크를 가로지른 이벤트를 근거로 올리는 도구를 소유한다."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from .context import RecipeToolContext
from .provenance import add_events, loaded

SEARCH_EVENTS = "search_events"
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
MAX_SEARCH_OFFSET = 9_900

# 검색 필터로 노출하는 이벤트 종류이며 계약이 목록을 소유한다.
TimelineEventKind = Literal[
    "execute_tool",
    "plan",
    "agent_tracer.action.logged",
    "agent_tracer.rule.logged",
    "agent_tracer.thought.logged",
    "agent_tracer.context.saved",
    "agent_tracer.context.snapshot",
    "agent_tracer.user.prompt.expansion",
    "agent_tracer.permission.request",
    "agent_tracer.worktree.remove",
    "agent_tracer.setup.triggered",
    "agent_tracer.file.changed",
    "agent_tracer.user.message",
    "agent_tracer.assistant.commentary",
    "agent_tracer.assistant.response",
    "agent_tracer.question.logged",
    "agent_tracer.todo.logged",
    "invoke_agent",
    "agent_tracer.instructions.loaded",
]


class SearchEventsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: TrimmedStr = Field(min_length=1, description="Search query")
    taskId: TrimmedStr | None = Field(default=None, min_length=1, description="Optional task ID filter")
    kind: TimelineEventKind | None = Field(
        default=None,
        description="Optional event kind filter. Must be one of the known event kinds.",
    )
    toolName: TrimmedStr | None = Field(default=None, description="Optional tool name filter")
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT, description="Max results per call"
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=MAX_SEARCH_OFFSET,
        description=("How many ranked results to skip; combine with limit to page beyond the first batch"),
    )


SEARCH_EVENTS_DESCRIPTION = (
    "Search indexed events by title/body, ranked by recency. Use q with optional taskId, kind, or "
    "toolName filters to find user corrections, instructions, and friction evidence. Pick limit "
    f"(up to {MAX_SEARCH_LIMIT} per call) and offset to page through as many results as you need."
)


class SearchEventsTool(AgentTool[SearchEventsArgs, RecipeToolContext]):
    """교정과 지시와 마찰 근거 이벤트를 찾고 돌려준 이벤트를 근거로 올린다."""

    name = SEARCH_EVENTS
    description = SEARCH_EVENTS_DESCRIPTION
    args_model = SearchEventsArgs
    transient_errors = TRANSIENT_TRACER_ERRORS

    async def execute(self, args: SearchEventsArgs, context: RecipeToolContext) -> str:
        result = await context.search.search_events(
            args.q, args.limit, args.offset, args.taskId, args.kind, args.toolName
        )
        return json.dumps(result, ensure_ascii=False)

    def record(self, args: SearchEventsArgs, content: str, context: RecipeToolContext, /) -> None:
        parsed = loaded(content)
        if isinstance(parsed, dict):
            add_events(context.catalog, parsed.get("events"), args.taskId)
