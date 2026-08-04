"""쓰기 도구 호출을 확인 대기 행으로 세우고 사용자의 결정으로 해소한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...runtime.dependencies import ExecutionSql, UserId
from ...runtime.ledger import SqlRow
from ...shared.wire import SuccessEnvelope, error_responses, ok
from ..dependencies import Dispatch, ToolExecutor, Updates
from ..intake.cancel import UpdateSignal
from ..intake.dispatch import ExecutionDispatch
from ..intake.follow_up import follow_up_client_request_id, follow_up_input_hash
from ..intake.ids import generate_ulid
from ..intake.ledger import ChatIntakeLedger
from ..intake.models import execution_dto
from ..intake.turn import ChatIntakeRejected
from ..tools.surface import chat_tool_note
from .access import CONFIRMATION_NOT_FOUND, CONFIRMATION_RESOLVED, owned_thread
from .envelope import CREATED_STATUS, invalid_request, read_payload, rejection
from .ledger import APPROVED, REJECTED, ChatSurfaceLedger
from .models import DecideToolBody, ProposeToolBody
from .threads import CHAT_THREAD_PATH
from .tool_calls import CONFIRMABLE_TOOLS, ChatToolArgsInvalid, plan_chat_tool_call
from .tool_client import ChatToolExecutor, ChatToolFailed

CHAT_CONFIRMATIONS_PATH = f"{CHAT_THREAD_PATH}/confirmations"
CHAT_CONFIRMATION_PATH = f"{CHAT_CONFIRMATIONS_PATH}/{{confirmation_id}}"

TOOL_UNAVAILABLE = (502, "chat.tool-failed", "Approved tool call did not succeed")

_SUMMARY_VALUE_LIMIT = 80

router = APIRouter()


@router.post(
    CHAT_CONFIRMATIONS_PATH,
    status_code=CREATED_STATUS,
    response_model=SuccessEnvelope,
    responses=error_responses(400, 404),
)
async def propose_chat_tool(
    thread_id: str, request: Request, source: ExecutionSql, user_id: UserId, updates: Updates
) -> JSONResponse:
    """확인이 필요한 도구 호출 하나를 실행하지 않고 대기 행에 세운다."""
    body = await read_payload(request, ProposeToolBody)
    if isinstance(body, JSONResponse):
        return body
    if body.toolName not in CONFIRMABLE_TOOLS:
        return invalid_request()
    args = dict(body.args)
    try:
        # 승인 뒤에야 인자가 어긋난 것을 알면 사용자가 부를 수 없는 행을 승인하게 된다.
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
            await _announce(updates, ledger, thread_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
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
    executor: ToolExecutor,
    dispatch: Dispatch,
) -> JSONResponse:
    """대기 중인 도구 호출 하나를 승인이나 거절로 해소한다."""
    body = await read_payload(request, DecideToolBody)
    if isinstance(body, JSONResponse):
        return body
    try:
        async with source.connect() as sql:
            return await _resolve(
                ChatSurfaceLedger(sql),
                ChatIntakeLedger(sql),
                executor,
                dispatch,
                updates,
                user_id,
                thread_id,
                confirmation_id,
                body,
            )
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    except ChatToolArgsInvalid:
        return invalid_request()
    except ChatToolFailed:
        return rejection(ChatIntakeRejected(*TOOL_UNAVAILABLE))


async def _resolve(
    ledger: ChatSurfaceLedger,
    intake: ChatIntakeLedger,
    executor: ChatToolExecutor,
    dispatch: ExecutionDispatch,
    updates: UpdateSignal | None,
    user_id: str,
    thread_id: str,
    confirmation_id: str,
    body: DecideToolBody,
) -> JSONResponse:
    now = datetime.now(UTC)
    await owned_thread(ledger, user_id, thread_id)
    pending = await _pending(ledger, thread_id, confirmation_id)
    tool_name = str(pending["tool_name"])
    if body.decision == "reject":
        content = f"User rejected the proposed {tool_name}. It was not executed."
        status = REJECTED
    else:
        # 실행이 먼저 성공해야 승인으로 전이하며 실패하면 대기 행이 남아 다시 물을 수 있다.
        content = await executor.execute(user_id, tool_name, dict(pending["args"] or {}))
        status = APPROVED
    resolved = await ledger.resolve_pending_tool(confirmation_id, status, now)
    if resolved is None:
        raise ChatIntakeRejected(*CONFIRMATION_RESOLVED)
    anchor = await ledger.insert_tool_message(generate_ulid(now), thread_id, content, confirmation_id, now)
    execution = (
        None
        if status == REJECTED
        # 거절로 이미 답한 자리라 이어 말할 턴을 세우지 않는다.
        else await _follow_up(ledger, intake, dispatch, user_id, thread_id, confirmation_id, anchor, now)
    )
    await _announce(updates, ledger, thread_id)
    return ok(
        {
            "confirmationId": confirmation_id,
            "toolName": tool_name,
            "status": resolved["status"],
            "result": content,
            "execution": None if execution is None else execution_dto(execution),
        }
    )


async def _follow_up(
    ledger: ChatSurfaceLedger,
    intake: ChatIntakeLedger,
    dispatch: ExecutionDispatch,
    user_id: str,
    thread_id: str,
    confirmation_id: str,
    anchor: str,
    now: datetime,
) -> SqlRow | None:
    """실행한 결과를 모델이 읽고 이어 말하도록 그 결과를 앵커로 삼는 턴을 세운다."""
    # 이미 실행 중인 턴이 있으면 그 턴이 결과를 이력으로 읽으므로 줄을 하나 더 세우지 않는다.
    if await ledger.latest_active_execution(thread_id) is not None:
        return None
    previous = await ledger.list_executions(thread_id)
    execution = await intake.insert_queued_execution(
        generate_ulid(now),
        user_id,
        thread_id,
        anchor,
        follow_up_client_request_id(confirmation_id),
        follow_up_input_hash(confirmation_id),
        previous[0]["model"] if previous else None,
        previous[0]["language"] if previous else None,
        now,
    )
    await dispatch.start(str(execution["id"]), thread_id)
    return execution


async def _pending(ledger: ChatSurfaceLedger, thread_id: str, confirmation_id: str) -> dict[str, Any]:
    pending = await ledger.find_pending_tool(confirmation_id)
    # 남의 스레드에 걸린 확인은 존재 자체를 알리지 않는다.
    if pending is None or pending["thread_id"] != thread_id:
        raise ChatIntakeRejected(*CONFIRMATION_NOT_FOUND)
    if pending["status"] != "pending":
        raise ChatIntakeRejected(*CONFIRMATION_RESOLVED)
    return pending


async def _announce(updates: UpdateSignal | None, ledger: ChatSurfaceLedger, thread_id: str) -> None:
    """확인 대기는 스레드 것이므로 지금 열려 있는 실행 채널에 실어 다른 연결이 그것을 본다."""
    if updates is None:
        return
    active = await ledger.latest_active_execution(thread_id)
    if active is not None:
        await updates.publish(active, {"executionId": active})


def _summarize(tool_name: str, args: dict[str, Any]) -> str:
    """사용자가 무엇을 승인하는지 한눈에 읽도록 인자를 한 줄로 줄인다."""
    parts = [f"{key}={_formatted(value)}" for key, value in args.items()]
    return f"{tool_name}({', '.join(parts)})" if parts else tool_name


def _formatted(value: Any) -> str:
    if isinstance(value, str):
        return f"{value[: _SUMMARY_VALUE_LIMIT - 3]}..." if len(value) > _SUMMARY_VALUE_LIMIT else value
    return json.dumps(value, ensure_ascii=False)
