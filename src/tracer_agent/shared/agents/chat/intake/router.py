"""브라우저의 접수 요청을 계약이 정한 경로와 봉투와 오류 형식으로 받는다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...runtime.dependencies import ExecutionSql, UserId
from ...shared.wire import SuccessEnvelope, error_envelope, error_responses, read_body, validation_details
from ..dependencies import Dispatch, Updates
from .cancel import ChatTurnCancellation
from .models import PostMessagePayload, execution_dto, message_dto
from .turn import ChatIntakeRejected, ChatTurnIntake

CHAT_THREADS_PATH = "/api/agent/chat/threads"
CHAT_MESSAGES_PATH = f"{CHAT_THREADS_PATH}/{{thread_id}}/messages"
CHAT_CANCEL_PATH = f"{CHAT_THREADS_PATH}/{{thread_id}}/executions/{{execution_id}}/cancel"
ACCEPTED_STATUS = 202
INVALID_REQUEST = (400, "validation_error", "Invalid request")

router = APIRouter()


@router.post(
    CHAT_MESSAGES_PATH,
    status_code=ACCEPTED_STATUS,
    response_model=SuccessEnvelope,
    responses=error_responses(400, 404, 409),
)
async def enqueue_chat_turn(
    thread_id: str, request: Request, source: ExecutionSql, user_id: UserId, dispatch: Dispatch
) -> JSONResponse:
    """대화 턴 하나를 접수하고 결과나 사유를 계약이 정한 봉투로 낸다."""
    body = await read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        payload = PostMessagePayload.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))

    try:
        async with source.connect() as sql:
            accepted = await ChatTurnIntake(sql, dispatch).enqueue(
                user_id,
                thread_id,
                payload,
                datetime.now(UTC),
            )
    except ChatIntakeRejected as rejected:
        return error_envelope(rejected.status, rejected.code, rejected.message)

    return JSONResponse(
        status_code=ACCEPTED_STATUS,
        content={
            "ok": True,
            "data": {
                "message": message_dto(accepted.message),
                "execution": execution_dto(accepted.execution),
            },
        },
    )


@router.post(CHAT_CANCEL_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def cancel_chat_turn(
    thread_id: str,
    execution_id: str,
    source: ExecutionSql,
    user_id: UserId,
    dispatch: Dispatch,
    updates: Updates,
) -> JSONResponse:
    """진행 중인 턴 하나를 끊고 결과나 사유를 계약이 정한 봉투로 낸다."""
    try:
        async with source.connect() as sql:
            canceled = await ChatTurnCancellation(sql, dispatch, updates).cancel(
                user_id,
                thread_id,
                execution_id,
                datetime.now(UTC),
            )
    except ChatIntakeRejected as rejected:
        return error_envelope(rejected.status, rejected.code, rejected.message)

    return JSONResponse(status_code=200, content={"ok": True, "data": {"execution": execution_dto(canceled)}})
