"""브라우저의 접수 요청을 계약이 정한 경로와 봉투와 오류 형식으로 받는다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...runtime.dependencies import ExecutionSql, UserId
from ..dependencies import Dispatch, Updates
from .cancel import ChatTurnCancellation
from .models import PostMessagePayload, execution_dto, message_dto
from .turn import ChatIntakeRejected, ChatTurnIntake

CHAT_THREADS_PATH = "/api/agent/chat/threads"
CHAT_MESSAGES_PATH = f"{CHAT_THREADS_PATH}/{{thread_id}}/messages"
CHAT_CANCEL_PATH = f"{CHAT_THREADS_PATH}/{{thread_id}}/executions/{{execution_id}}/cancel"
ACCEPTED_STATUS = 202
INVALID_REQUEST = (400, "validation_error", "Invalid request")


async def enqueue_chat_turn(
    thread_id: str, request: Request, source: ExecutionSql, user_id: UserId, dispatch: Dispatch
) -> JSONResponse:
    """대화 턴 하나를 접수하고 결과나 사유를 계약이 정한 봉투로 낸다."""
    body = await _read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        payload = PostMessagePayload.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=_details(invalid))

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


async def cancel_chat_turn(
    thread_id: str,
    execution_id: str,
    source: ExecutionSql,
    user_id: UserId,
    dispatch: Dispatch,
    updates: Updates,
) -> JSONResponse:
    """도는 턴 하나를 끊고 결과나 사유를 계약이 정한 봉투로 낸다."""
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


def error_envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    """실패 사유를 계약이 정한 오류 봉투로 적는다."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"ok": False, "error": error})


async def _read_body(request: Request) -> dict[str, Any] | None:
    try:
        body = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _details(invalid: ValidationError) -> Any:
    return json.loads(invalid.json(include_url=False, include_context=False, include_input=False))
