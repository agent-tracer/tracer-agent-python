"""정리 제안의 조회와 해소를 계약이 정한 경로와 봉투로 받는다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from ..runtime.dependencies import ExecutionSql, UserId
from ..shared.tracer_window import UpstreamRejected
from ..shared.wire import (
    INVALID_REQUEST,
    SuccessEnvelope,
    error_envelope,
    error_responses,
    ok,
    validation_details,
)
from .archiver import CleanupTaskArchiver
from .models import CleanupRejected
from .service import accept_cleanup_suggestion, dismiss_cleanup_suggestion, list_cleanup_suggestions
from .store import CleanupSuggestionStore

CLEANUP_SUGGESTIONS_PATH = "/api/agent/cleanup/suggestions"
CLEANUP_SUGGESTION_PATH = f"{CLEANUP_SUGGESTIONS_PATH}/{{suggestion_id}}"
CLEANUP_ACCEPT_PATH = f"{CLEANUP_SUGGESTION_PATH}/accept"
CLEANUP_DISMISS_PATH = f"{CLEANUP_SUGGESTION_PATH}/dismiss"

router = APIRouter()


class ListSuggestionsQuery(BaseModel):
    """목록 창구가 받는 질의이며 상태를 싣지 않으면 모든 상태를 낸다."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["pending", "accepted", "dismissed"] | None = None


def get_task_archiver(request: Request) -> CleanupTaskArchiver:
    """앱 수명이 세운 추적 보관 창구를 낸다."""
    archiver: CleanupTaskArchiver = request.app.state.services.task_archiver
    return archiver


TaskArchiver = Annotated[CleanupTaskArchiver, Depends(get_task_archiver)]


def _rejection(rejected: CleanupRejected) -> JSONResponse:
    return error_envelope(rejected.status, rejected.code, rejected.message)


def _upstream(rejected: UpstreamRejected) -> JSONResponse:
    """추적이 낸 상태와 코드를 그대로 부른 쪽에 낸다."""
    return error_envelope(rejected.status, rejected.code, rejected.message)


@router.get(CLEANUP_SUGGESTIONS_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def list_cleanup_suggestions_window(
    request: Request, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """이 사용자의 정리 제안을 상태로 걸러 낸다."""
    try:
        query = ListSuggestionsQuery.model_validate(dict(request.query_params))
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))
    async with source.connect() as sql:
        data = await list_cleanup_suggestions(CleanupSuggestionStore(sql), user_id, query.status)
    return ok(data)


@router.post(CLEANUP_ACCEPT_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def accept_cleanup_suggestion_window(
    suggestion_id: str, source: ExecutionSql, archiver: TaskArchiver, user_id: UserId
) -> JSONResponse:
    """정리 제안을 수용하고 그 태스크의 보관을 추적에 요청한다."""
    try:
        async with source.connect() as sql:
            data = await accept_cleanup_suggestion(
                CleanupSuggestionStore(sql), archiver, user_id, suggestion_id, datetime.now(UTC)
            )
    except CleanupRejected as rejected:
        return _rejection(rejected)
    except UpstreamRejected as rejected:
        return _upstream(rejected)
    return ok(data)


@router.post(CLEANUP_DISMISS_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def dismiss_cleanup_suggestion_window(
    suggestion_id: str, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """정리 제안을 기각한다."""
    try:
        async with source.connect() as sql:
            data = await dismiss_cleanup_suggestion(
                CleanupSuggestionStore(sql), user_id, suggestion_id, datetime.now(UTC)
            )
    except CleanupRejected as rejected:
        return _rejection(rejected)
    return ok(data)
