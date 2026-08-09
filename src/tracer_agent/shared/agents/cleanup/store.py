"""정리 제안 원장을 읽고 쓰는 문장 한 벌을 소유한다."""

from __future__ import annotations

from datetime import datetime

from ..runtime.ledger import LedgerSql, SqlRow
from .models import CLEANUP_STATUS_PENDING, CleanupSuggestion

_SUGGESTION_COLUMNS = """
id, user_id, job_id, task_id, kind, current_value, proposed_value, rationale, status, error,
created_at, resolved_at, observed_last_event_at
"""

_INSERT_SUGGESTION = """
INSERT INTO task_cleanup_suggestions (
    id, user_id, job_id, task_id, kind, current_value, proposed_value, rationale, status, error,
    created_at, resolved_at, observed_last_event_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
"""

_UPDATE_RESOLUTION = """
UPDATE task_cleanup_suggestions SET status = $2, resolved_at = $3
 WHERE id = $1
 RETURNING id
"""

_REFRESH_PENDING = """
UPDATE task_cleanup_suggestions
   SET job_id = $2, rationale = $3, observed_last_event_at = $4
 WHERE id = $1
 RETURNING id
"""

_SELECT_BY_ID = f"""
SELECT {_SUGGESTION_COLUMNS} FROM task_cleanup_suggestions
 WHERE id = $1
"""

_SELECT_BY_USER_STATUS = f"""
SELECT {_SUGGESTION_COLUMNS} FROM task_cleanup_suggestions
 WHERE user_id = $1 AND status = $2
 ORDER BY created_at DESC
"""

_SELECT_PENDING_BY_TASK_KIND = f"""
SELECT {_SUGGESTION_COLUMNS} FROM task_cleanup_suggestions
 WHERE user_id = $1 AND task_id = $2 AND kind = $3 AND status = $4
"""


def to_suggestion(row: SqlRow) -> CleanupSuggestion:
    """원장 행 하나를 정리 제안 모델로 읽는다."""
    return CleanupSuggestion(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        job_id=str(row["job_id"]),
        task_id=str(row["task_id"]),
        kind=str(row["kind"]),
        rationale=str(row["rationale"]),
        status=str(row["status"]),
        created_at=row["created_at"],
        current_value=row["current_value"],
        proposed_value=row["proposed_value"],
        error=row["error"],
        resolved_at=row["resolved_at"],
        observed_last_event_at=row["observed_last_event_at"],
    )


class CleanupSuggestionStore:
    """정리 제안 원장의 조회와 저장을 제공한다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def find_by_id(self, suggestion_id: str) -> CleanupSuggestion | None:
        """제안 하나를 읽으며 없으면 아무것도 내지 않는다."""
        rows = await self._sql.fetch(_SELECT_BY_ID, suggestion_id)
        return to_suggestion(rows[0]) if rows else None

    async def find_by_user_status(self, user_id: str, status: str) -> list[CleanupSuggestion]:
        """그 사용자의 그 상태 제안을 만든 시각의 내림차순으로 읽는다."""
        rows = await self._sql.fetch(_SELECT_BY_USER_STATUS, user_id, status)
        return [to_suggestion(row) for row in rows]

    async def find_pending(self, user_id: str, task_id: str, kind: str) -> CleanupSuggestion | None:
        """그 태스크와 종류에 대기 중인 제안이며 유일 색인이 하나만 남긴다."""
        rows = await self._sql.fetch(
            _SELECT_PENDING_BY_TASK_KIND, user_id, task_id, kind, CLEANUP_STATUS_PENDING
        )
        return to_suggestion(rows[0]) if rows else None

    async def insert(self, suggestion: CleanupSuggestion) -> None:
        """제안 한 행을 새로 적는다."""
        await self._sql.fetch(
            _INSERT_SUGGESTION,
            suggestion.id,
            suggestion.user_id,
            suggestion.job_id,
            suggestion.task_id,
            suggestion.kind,
            suggestion.current_value,
            suggestion.proposed_value,
            suggestion.rationale,
            suggestion.status,
            suggestion.error,
            suggestion.created_at,
            suggestion.resolved_at,
            suggestion.observed_last_event_at,
        )

    async def save_resolution(self, suggestion: CleanupSuggestion) -> None:
        """해소 상태와 해소 시각만 그 행에 적는다."""
        await self._sql.fetch(_UPDATE_RESOLUTION, suggestion.id, suggestion.status, suggestion.resolved_at)

    async def refresh_pending(
        self, suggestion_id: str, job_id: str, rationale: str, observed_last_event_at: datetime | None
    ) -> None:
        """같은 태스크와 종류의 대기 행 하나에 새 근거와 새 관측 시각을 적는다."""
        await self._sql.fetch(_REFRESH_PENDING, suggestion_id, job_id, rationale, observed_last_event_at)
