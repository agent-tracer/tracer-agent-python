"""정리 후보 배치를 커서 페이지로 잘라 보여주고 노출한 후보를 근거로 올린다."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from tracer_agent.shared.agents.shared.models import TrimmedStr
from tracer_agent.shared.agents.task_cleanup.models import CandidatePage, CleanupBatch, CleanupCandidate

from ...runtime.tooling import AgentTool

LIST_CANDIDATE_TASKS = "list_candidate_tasks"
DEFAULT_CANDIDATE_LIMIT = 30
MAX_CANDIDATE_LIMIT = 100


class ListCandidateTasksArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CANDIDATE_LIMIT,
        description=(
            f"Max candidates in this page (default {DEFAULT_CANDIDATE_LIMIT}, hard cap {MAX_CANDIDATE_LIMIT})"
        ),
    )
    cursor: TrimmedStr | None = Field(
        default=None,
        min_length=1,
        description=(
            "Opaque cursor from a previous call's nextCursor. Omit to start from the first candidate."
        ),
    )


LIST_CANDIDATE_TASKS_DESCRIPTION = (
    "List the cleanup candidates the server already qualified for this scan (hidden, active, and "
    "recently touched tasks are excluded before you see them). Each entry carries visibleTitle, "
    "status, lastEventAt, hasEvents, activeChildCount and the server-detected candidateReasons. "
    "Call this first, and page with cursor until truncated is false if you want the whole batch. "
    "Only task ids returned here may be proposed. moreCandidatesOutsideBatch=true means the server "
    "itself capped this batch; the leftover tasks are outside your reach and a future scan will "
    "pick them up."
)


def candidate_page(batch: CleanupBatch, limit: int | None, cursor: str | None) -> CandidatePage:
    """서버가 미리 자격 심사한 후보 배치를 커서로 잘라 한 페이지를 낸다."""
    size = limit if limit is not None else DEFAULT_CANDIDATE_LIMIT
    start = int(cursor) if cursor is not None and cursor.isdigit() else 0
    page = batch.candidates[start : start + size]
    next_index = start + len(page)
    truncated = next_index < len(batch.candidates)
    return CandidatePage(
        candidates=page,
        truncated=truncated,
        nextCursor=str(next_index) if truncated else None,
        total=len(batch.candidates),
        moreCandidatesOutsideBatch=batch.batchTruncated,
    )


class ListCandidateTasksTool(AgentTool[ListCandidateTasksArgs]):
    """후보 배치를 페이지로 내주고 노출한 후보만 인용 가능한 근거로 올린다."""

    name = LIST_CANDIDATE_TASKS
    description = LIST_CANDIDATE_TASKS_DESCRIPTION
    args_model = ListCandidateTasksArgs

    def __init__(self, batch: CleanupBatch, exposed: dict[str, CleanupCandidate]) -> None:
        self._batch = batch
        self._exposed = exposed

    async def execute(self, args: ListCandidateTasksArgs) -> str:
        page = candidate_page(self._batch, args.limit, args.cursor)
        dumped = page.model_dump(mode="json")
        # null lastEventAt은 이벤트 없음을 뜻해 남기고 nextCursor는 없을 때만 뺀다.
        if dumped["nextCursor"] is None:
            del dumped["nextCursor"]
        return json.dumps(dumped, ensure_ascii=False)

    def record(self, _args: ListCandidateTasksArgs, content: str, /) -> None:
        for candidate in CandidatePage.model_validate_json(content).candidates:
            self._exposed[candidate.id] = candidate
