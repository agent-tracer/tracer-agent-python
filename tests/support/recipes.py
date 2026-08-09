"""레시피와 정리 제안 시험이 창구 뒤에 세우는 대역을 소유한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tracer_agent.shared.agents.cleanup.models import (
    CLEANUP_STATUS_PENDING,
    CLEANUP_SUGGESTION_KIND_ARCHIVE,
    CleanupSuggestion,
)
from tracer_agent.shared.agents.recipe.models import (
    RECIPE_EDITOR_AGENT,
    RECIPE_INJECTED_VIA_PULL,
    RECIPE_STATUS_CANDIDATE,
    Recipe,
    RecipeApplication,
)
from tracer_agent.shared.agents.recipe.search import RecipeSearchHit
from tracer_agent.shared.agents.shared.json_view import JsonObject
from tracer_agent.shared.agents.shared.tracer_window import UpstreamRejected

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def recipe_row(**overrides: Any) -> Recipe:
    """시험이 원장에 세우는 레시피 한 행이다."""
    row = Recipe(
        id="recipe-1",
        user_id="local",
        status=RECIPE_STATUS_CANDIDATE,
        title="안전한 마이그레이션",
        intent="스키마 변경을 안전하게 넣는다",
        description="저장된 모양이 바뀔 때 쓴다",
        summary_md="- 변경을 정의한다",
        request="사용자가 마이그레이션 추가를 요청했다",
        rev=1,
        created_at=NOW,
        updated_at=NOW,
        contributing_slices=[{"taskId": "task-1", "turnIds": [], "eventIds": []}],
        last_edited_by=RECIPE_EDITOR_AGENT,
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def application_row(**overrides: Any) -> RecipeApplication:
    """시험이 원장에 세우는 적용 이력 한 행이다."""
    row = RecipeApplication(
        id="application-1",
        user_id="local",
        recipe_id="recipe-1",
        task_id="task-1",
        injected_via=RECIPE_INJECTED_VIA_PULL,
        created_at=NOW,
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def suggestion_row(**overrides: Any) -> CleanupSuggestion:
    """시험이 원장에 세우는 정리 제안 한 행이다."""
    row = CleanupSuggestion(
        id="suggestion-1",
        user_id="local",
        job_id="job-1",
        task_id="task-1",
        kind=CLEANUP_SUGGESTION_KIND_ARCHIVE,
        rationale="사건이 오래 없다",
        status=CLEANUP_STATUS_PENDING,
        created_at=NOW,
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


@dataclass
class RecordingSearch:
    """색인을 세우지 않고 시험이 정한 적중을 그대로 낸다."""

    hits: list[RecipeSearchHit] = field(default_factory=list)
    calls: list[tuple[str, str, int]] = field(default_factory=list)

    async def search(self, user_id: str, q: str, limit: int) -> list[RecipeSearchHit]:
        """부른 인자를 기록하고 준비된 적중을 낸다."""
        self.calls.append((user_id, q, limit))
        return self.hits


@dataclass
class RecordingTaskReader:
    """추적을 부르지 않고 시험이 정한 제목 표를 낸다."""

    titles: dict[str, str] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    async def titles_by_ids(self, _user_id: str, ids: list[str]) -> dict[str, str]:
        """부른 식별자를 기록하고 그중 아는 제목만 낸다."""
        self.calls.append(list(ids))
        return {task_id: self.titles[task_id] for task_id in ids if task_id in self.titles}


@dataclass
class RecordingArchiver:
    """추적을 부르지 않고 보관 요청을 기록하며 시험이 정한 거절을 낸다."""

    rejection: UpstreamRejected | None = None
    calls: list[tuple[str, str, datetime | None]] = field(default_factory=list)

    async def archive(self, user_id: str, task_id: str, if_no_activity_since: datetime | None) -> None:
        """부른 인자를 기록하고 시험이 정한 거절이 있으면 그것을 낸다."""
        self.calls.append((user_id, task_id, if_no_activity_since))
        if self.rejection is not None:
            raise self.rejection


@dataclass
class RecordingSearchIndex:
    """색인을 세우지 않고 배출이 쓴 문서와 지운 문서를 기록한다."""

    indexed: list[tuple[str, str, JsonObject]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    async def index_document(self, alias: str, document_id: str, document: JsonObject) -> None:
        """색인 쓰기를 기록하며 시험이 정한 식별자에는 실패를 낸다."""
        if document_id in self.fail_on:
            raise RuntimeError("search index is down")
        self.indexed.append((alias, document_id, document))

    async def delete_document(self, alias: str, document_id: str) -> None:
        """색인 삭제를 기록하며 시험이 정한 식별자에는 실패를 낸다."""
        if document_id in self.fail_on:
            raise RuntimeError("search index is down")
        self.deleted.append((alias, document_id))
