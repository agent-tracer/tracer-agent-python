"""앵커와 제목이 닮은 태스크를 추적 창구로 찾는 도구를 소유한다."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.json_view import text
from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ...runtime.tracer_client import TRANSIENT_TRACER_ERRORS
from .context import RecipeToolContext

FIND_SIMILAR_TASKS = "find_similar_tasks"
DEFAULT_SIMILAR_LIMIT = 5
MAX_SIMILAR_LIMIT = 20


class FindSimilarTasksArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchorTaskId: TrimmedStr = Field(min_length=1, description="The anchor task ID")
    limit: int = Field(default=DEFAULT_SIMILAR_LIMIT, ge=1, le=MAX_SIMILAR_LIMIT, description="Max tasks")


FIND_SIMILAR_TASKS_DESCRIPTION = (
    "Find tasks with titles similar to the anchor task. Use after inspecting the anchor to check "
    "whether the workflow repeats."
)


class FindSimilarTasksTool(AgentTool[FindSimilarTasksArgs, RecipeToolContext]):
    """앵커의 제목을 읽어 제목이 닮은 태스크를 찾는다."""

    name = FIND_SIMILAR_TASKS
    description = FIND_SIMILAR_TASKS_DESCRIPTION
    args_model = FindSimilarTasksArgs
    transient_errors = TRANSIENT_TRACER_ERRORS

    async def execute(self, args: FindSimilarTasksArgs, context: RecipeToolContext) -> str:
        anchor = await context.reader.task_with_events(args.anchorTaskId, 1)
        if anchor is None:
            return f"Task {args.anchorTaskId} not found."
        similar = await context.search.similar_tasks(
            text(anchor.task["title"]), args.anchorTaskId, args.limit
        )
        return json.dumps(similar, ensure_ascii=False)
