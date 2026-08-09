"""정리 제안의 조회와 해소를 한 벌의 유스케이스로 소유한다."""

from __future__ import annotations

from datetime import datetime

from ..shared.json_view import JsonObject
from .archiver import CleanupTaskArchiver
from .models import (
    CLEANUP_STATUS_PENDING,
    CLEANUP_SUGGESTION_NOT_FOUND,
    CLEANUP_SUGGESTION_STATUSES,
    CleanupRejected,
    CleanupSuggestion,
    cleanup_suggestion_view,
)
from .store import CleanupSuggestionStore


async def owned_suggestion(
    store: CleanupSuggestionStore, user_id: str, suggestion_id: str
) -> CleanupSuggestion:
    """남의 제안은 존재 자체를 알리지 않으므로 없는 것과 같은 거절을 낸다."""
    suggestion = await store.find_by_id(suggestion_id)
    if suggestion is None or suggestion.user_id != user_id:
        raise CleanupRejected(CLEANUP_SUGGESTION_NOT_FOUND)
    return suggestion


async def list_cleanup_suggestions(
    store: CleanupSuggestionStore, user_id: str, status: str | None
) -> JsonObject:
    """정리 제안을 상태로 걸러 내며 대기 행은 태스크와 종류의 쌍으로 한 벌만 남긴다."""
    rows = _dedupe_pending(await _collect(store, user_id, status))
    return {"suggestions": [cleanup_suggestion_view(row) for row in rows]}


async def _collect(
    store: CleanupSuggestionStore, user_id: str, status: str | None
) -> list[CleanupSuggestion]:
    """상태를 싣지 않으면 선언 순서로 이어 붙이고 전체를 다시 정렬하지 않는다."""
    if status is not None:
        return await store.find_by_user_status(user_id, status)
    found: list[CleanupSuggestion] = []
    for declared in CLEANUP_SUGGESTION_STATUSES:
        found.extend(await store.find_by_user_status(user_id, declared))
    return found


def _dedupe_pending(rows: list[CleanupSuggestion]) -> list[CleanupSuggestion]:
    """다른 상태의 행은 중복 제거 대상이 아니다."""
    seen: set[tuple[str, str]] = set()
    kept: list[CleanupSuggestion] = []
    for row in rows:
        if row.status != CLEANUP_STATUS_PENDING:
            kept.append(row)
            continue
        key = (row.task_id, row.kind)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


async def accept_cleanup_suggestion(
    store: CleanupSuggestionStore,
    archiver: CleanupTaskArchiver,
    user_id: str,
    suggestion_id: str,
    now: datetime,
) -> JsonObject:
    """제안을 수용으로 적은 뒤 추적에 조건부 보관을 요청하고 거절을 받으면 그 수용을 되돌린다."""
    suggestion = await owned_suggestion(store, user_id, suggestion_id)
    # 끊긴 뒤의 재시도가 보관만 다시 밟도록 이미 수용된 제안은 원장을 바꾸지 않는다.
    if suggestion.is_accepted():
        await _archive(archiver, user_id, suggestion)
        return {"suggestion": cleanup_suggestion_view(suggestion)}

    suggestion.accept(now)
    await store.save_resolution(suggestion)
    try:
        await _archive(archiver, user_id, suggestion)
    except Exception:
        suggestion.revert_acceptance()
        await store.save_resolution(suggestion)
        raise
    return {"suggestion": cleanup_suggestion_view(suggestion)}


async def _archive(archiver: CleanupTaskArchiver, user_id: str, suggestion: CleanupSuggestion) -> None:
    await archiver.archive(user_id, suggestion.task_id, suggestion.observed_last_event_at)


async def dismiss_cleanup_suggestion(
    store: CleanupSuggestionStore, user_id: str, suggestion_id: str, now: datetime
) -> JsonObject:
    """제안을 기각하며 태스크를 건드리지 않으므로 추적을 부르지 않는다."""
    suggestion = await owned_suggestion(store, user_id, suggestion_id)
    suggestion.dismiss(now)
    await store.save_resolution(suggestion)
    return {"suggestion": cleanup_suggestion_view(suggestion)}
