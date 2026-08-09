"""정리 제안 원장 행 하나의 상태 전이와 창구가 내는 칸을 소유한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..shared.instant import iso, opt_iso
from ..shared.json_view import JsonObject

CLEANUP_SUGGESTION_KIND_ARCHIVE = "archive"
CLEANUP_SUGGESTION_KINDS = (CLEANUP_SUGGESTION_KIND_ARCHIVE,)

CLEANUP_STATUS_PENDING = "pending"
CLEANUP_STATUS_ACCEPTED = "accepted"
CLEANUP_STATUS_DISMISSED = "dismissed"

# 목록 창구가 상태를 싣지 않았을 때 결과를 이어 붙이는 순서다.
CLEANUP_SUGGESTION_STATUSES = (
    CLEANUP_STATUS_PENDING,
    CLEANUP_STATUS_ACCEPTED,
    CLEANUP_STATUS_DISMISSED,
)

CLEANUP_SUGGESTION_NOT_FOUND = (404, "not_found", "Cleanup suggestion not found")
CLEANUP_NOT_PENDING = (409, "cleanup.not-pending", "Cleanup suggestion is not pending")
CLEANUP_STALE = (409, "cleanup.stale", "Task has activity since the suggestion observed it")


class CleanupRejected(Exception):
    """정리 창구가 요청을 받아들일 수 없어 계약이 정한 상태와 코드로 돌려보낸다."""

    def __init__(self, rejection: tuple[int, str, str]) -> None:
        status, code, message = rejection
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class CleanupSuggestion:
    """정리 제안 원장 행 하나이며 수용은 자기 원장에 적고 판정은 추적이 갖는다."""

    id: str
    user_id: str
    job_id: str
    task_id: str
    kind: str
    rationale: str
    status: str
    created_at: datetime
    current_value: str | None = None
    proposed_value: str | None = None
    error: str | None = None
    resolved_at: datetime | None = None
    # 제안이 관측한 그 태스크의 마지막 사건 시각이며 수용이 조건으로 실어 보낸다.
    observed_last_event_at: datetime | None = None

    def is_accepted(self) -> bool:
        """끊긴 뒤의 재시도가 원장을 다시 바꾸지 않도록 이미 수용된 제안인지 알린다."""
        return self.status == CLEANUP_STATUS_ACCEPTED

    def accept(self, now: datetime) -> None:
        """대기 중인 제안만 수용으로 옮기고 해소 시각을 적는다."""
        if self.status != CLEANUP_STATUS_PENDING:
            raise CleanupRejected(CLEANUP_NOT_PENDING)
        self.status = CLEANUP_STATUS_ACCEPTED
        self.resolved_at = now

    def revert_acceptance(self) -> None:
        """추적이 조건이 깨졌다고 알리면 수용을 되돌려 대기로 남긴다."""
        self.status = CLEANUP_STATUS_PENDING
        self.resolved_at = None

    def dismiss(self, now: datetime) -> None:
        """대기 중인 제안만 기각으로 옮기고 해소 시각을 적는다."""
        if self.status != CLEANUP_STATUS_PENDING:
            raise CleanupRejected(CLEANUP_NOT_PENDING)
        self.status = CLEANUP_STATUS_DISMISSED
        self.resolved_at = now


def cleanup_suggestion_view(suggestion: CleanupSuggestion) -> JsonObject:
    """조회와 해소가 모두 내는 제안 한 건의 칸이며 관측 시각은 싣지 않는다."""
    return {
        "id": suggestion.id,
        "userId": suggestion.user_id,
        "jobId": suggestion.job_id,
        "taskId": suggestion.task_id,
        "kind": suggestion.kind,
        "currentValue": suggestion.current_value,
        "proposedValue": suggestion.proposed_value,
        "rationale": suggestion.rationale,
        "status": suggestion.status,
        "error": suggestion.error,
        "createdAt": iso(suggestion.created_at),
        "resolvedAt": opt_iso(suggestion.resolved_at),
    }
