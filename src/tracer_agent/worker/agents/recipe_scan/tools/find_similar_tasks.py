"""앵커와 제목이 닮은 태스크를 원장과 색인으로 찾는 도구를 소유한다."""

from __future__ import annotations

import json

from asyncpg import CannotConnectNowError, PostgresConnectionError
from opensearchpy.exceptions import (
    ConnectionError as OpenSearchConnectionError,
)
from opensearchpy.exceptions import (
    ConnectionTimeout as OpenSearchConnectionTimeout,
)
from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr

from ...runtime.tooling import AgentTool
from ..reader import RecipeLedgerReader
from ..search import RecipeSearchReader

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


class FindSimilarTasksTool(AgentTool[FindSimilarTasksArgs]):
    """앵커의 제목을 원장에서 읽어 색인에서 닮은 태스크를 찾는다."""

    name = FIND_SIMILAR_TASKS
    description = FIND_SIMILAR_TASKS_DESCRIPTION
    args_model = FindSimilarTasksArgs
    # 앵커 조회는 원장(asyncpg), 유사 검색은 색인(opensearch)이라 두 백엔드의 연결 오류가 모두 일시적이다.
    transient_errors = (
        PostgresConnectionError,
        CannotConnectNowError,
        OpenSearchConnectionError,
        OpenSearchConnectionTimeout,
        ConnectionError,
        TimeoutError,
    )

    def __init__(self, reader: RecipeLedgerReader, search: RecipeSearchReader) -> None:
        self._reader = reader
        self._search = search

    async def execute(self, args: FindSimilarTasksArgs) -> str:
        anchor = await self._reader.task_with_events(args.anchorTaskId, 1)
        if anchor is None:
            return f"Task {args.anchorTaskId} not found."
        similar = await self._search.similar_tasks(anchor["task"]["title"], args.anchorTaskId, args.limit)
        return json.dumps(similar, ensure_ascii=False)
