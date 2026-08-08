"""쓰기 도구 호출을 확인 대기 행으로 세우고 사용자의 결정으로 해소한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...runtime.dependencies import ExecutionSql, UserId
from ...shared.json_view import JsonObject
from ...shared.wire import SuccessEnvelope, error_responses, ok
from ..dependencies import Dispatch, ToolExecutor, Updates, Watch
from ..intake.ids import generate_ulid
from ..rejections import ChatRejected
from ..tools.surface import chat_tool_note
from .access import owned_thread
from .envelope import CREATED_STATUS, argument_rejection, invalid_request, read_payload, rejection
from .ledger import ChatSurfaceLedger
from .models import DecideToolBody, ProposeToolBody
from .resolution import ChatConfirmationResolution, ResolvedConfirmation, ThreadUpdateAnnouncer
from .threads import CHAT_THREAD_PATH
from .tool_calls import (
    CONFIRMABLE_TOOLS,
    ChatToolArgsInvalid,
    missing_action_arguments,
    plan_chat_tool_call,
)
from .tool_client import ChatToolFailed
from .views import execution_dto

CHAT_CONFIRMATIONS_PATH = f"{CHAT_THREAD_PATH}/confirmations"
CHAT_CONFIRMATION_PATH = f"{CHAT_CONFIRMATIONS_PATH}/{{confirmation_id}}"

TOOL_UNAVAILABLE = (502, "chat.tool-failed", "Approved tool call did not succeed")

SERVER_ERROR_STATUS = 500

_SUMMARY_VALUE_LIMIT = 80

router = APIRouter()


@router.post(
    CHAT_CONFIRMATIONS_PATH,
    status_code=CREATED_STATUS,
    response_model=SuccessEnvelope,
    responses=error_responses(400, 404),
)
async def propose_chat_tool(
    thread_id: str,
    request: Request,
    source: ExecutionSql,
    user_id: UserId,
    updates: Updates,
    watch: Watch,
) -> JSONResponse:
    """확인이 필요한 도구 호출 하나를 실행하지 않고 대기 행에 세운다."""
    body = await read_payload(request, ProposeToolBody)
    if isinstance(body, JSONResponse):
        return body
    if body.toolName not in CONFIRMABLE_TOOLS:
        return invalid_request()
    args = dict(body.args)
    # 세운 뒤에 거절하면 사용자가 성공할 수 없는 일을 승인하고 그 턴의 값을 치른다.
    missing = missing_action_arguments(body.toolName, args)
    if missing:
        return argument_rejection(str(args.get("action", "")), missing)
    try:
        plan_chat_tool_call(body.toolName, args)
    except ChatToolArgsInvalid:
        return invalid_request()

    now = datetime.now(UTC)
    try:
        async with source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, user_id, thread_id)
            # 이 턴의 어시스턴트 메시지는 아직 적재 전이라 어느 메시지에 매인지 확정할 수 없다.
            pending = await ledger.insert_pending_tool(
                generate_ulid(now), thread_id, body.toolName, args, now
            )
    except ChatRejected as rejected:
        return rejection(rejected)
    await ThreadUpdateAnnouncer(source, updates, watch).announce(thread_id)
    return ok(
        {
            "confirmationId": pending["id"],
            "toolName": pending["tool_name"],
            "status": pending["status"],
            "summary": _summarize(body.toolName, args),
            "note": chat_tool_note("proposalNote"),
        },
        status=CREATED_STATUS,
    )


@router.post(
    CHAT_CONFIRMATION_PATH,
    response_model=SuccessEnvelope,
    responses=error_responses(400, 404, 409, 502),
)
async def decide_chat_tool(
    thread_id: str,
    confirmation_id: str,
    request: Request,
    source: ExecutionSql,
    user_id: UserId,
    updates: Updates,
    watch: Watch,
    executor: ToolExecutor,
    dispatch: Dispatch,
) -> JSONResponse:
    """대기 중인 도구 호출 하나를 승인이나 거절로 해소한다."""
    body = await read_payload(request, DecideToolBody)
    if isinstance(body, JSONResponse):
        return body
    now = datetime.now(UTC)
    resolution = ChatConfirmationResolution(
        source, executor, dispatch, ThreadUpdateAnnouncer(source, updates, watch)
    )
    try:
        resolved = await resolution.resolve(user_id, thread_id, confirmation_id, body.decision, now)
    except ChatRejected as rejected:
        return rejection(rejected)
    except ChatToolArgsInvalid:
        return invalid_request()
    except ChatToolFailed as failed:
        # 상류가 못 받은 실패와 내용 자체가 규칙에 걸린 거절은 사용자가 할 행동이 정반대다.
        if failed.status < SERVER_ERROR_STATUS:
            return invalid_request(failed.details)
        return rejection(ChatRejected(*TOOL_UNAVAILABLE))
    return ok(_decided(resolved))


def _decided(resolved: ResolvedConfirmation) -> JsonObject:
    """해소된 확인 하나를 계약이 정한 와이어 표현으로 바꾼다."""
    return {
        "confirmationId": resolved.confirmation_id,
        "toolName": resolved.tool_name,
        "status": resolved.status,
        "result": resolved.result,
        "execution": None if resolved.execution is None else execution_dto(resolved.execution),
    }


def _summarize(tool_name: str, args: dict[str, Any]) -> str:
    """사용자가 무엇을 승인하는지 한눈에 읽도록 인자를 한 줄로 줄인다."""
    parts = [f"{key}={_formatted(value)}" for key, value in args.items()]
    return f"{tool_name}({', '.join(parts)})" if parts else tool_name


def _formatted(value: Any) -> str:
    if isinstance(value, str):
        return f"{value[: _SUMMARY_VALUE_LIMIT - 3]}..." if len(value) > _SUMMARY_VALUE_LIMIT else value
    return json.dumps(value, ensure_ascii=False)
