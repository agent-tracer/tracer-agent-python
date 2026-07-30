"""쓰기 도구 호출을 확인 대기 행으로 세우고 사용자의 결정으로 해소한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ...runtime.ledger import SqlSource
from ..intake.cancel import UpdateSignal
from ..intake.ids import generate_ulid
from ..intake.router import MONITOR_USER_HEADER, resolve_user_id
from ..intake.turn import ChatIntakeRejected
from .access import CONFIRMATION_NOT_FOUND, CONFIRMATION_RESOLVED, owned_thread
from .envelope import CREATED_STATUS, invalid_request, ok, read_payload, rejection
from .ledger import APPROVED, REJECTED, ChatSurfaceLedger
from .models import DecideToolBody, ProposeToolBody
from .threads import CHAT_THREAD_PATH
from .tool_calls import CONFIRMABLE_TOOLS, ChatToolArgsInvalid
from .tool_client import ChatToolExecutor, ChatToolFailed

CHAT_CONFIRMATIONS_PATH = f"{CHAT_THREAD_PATH}/confirmations"
CHAT_CONFIRMATION_PATH = f"{CHAT_CONFIRMATIONS_PATH}/{{confirmation_id}}"

TOOL_UNAVAILABLE = (502, "chat.tool-failed", "Approved tool call did not succeed")

# 승인 전에는 실행되지 않았음을 모델이 오해하지 않게 두 구현체가 같은 문장을 보인다.
PROPOSAL_NOTE = (
    "Queued for user confirmation. This action has NOT run yet and will only run after the user "
    "approves it. Tell the user you are awaiting their confirmation; never claim the change is "
    "already done."
)

_SUMMARY_VALUE_LIMIT = 80


async def propose_chat_tool(thread_id: str, request: Request) -> JSONResponse:
    """확인이 필요한 도구 호출 하나를 실행하지 않고 대기 행에 세운다."""
    body = await read_payload(request, ProposeToolBody)
    if isinstance(body, JSONResponse):
        return body
    if body.toolName not in CONFIRMABLE_TOOLS:
        return invalid_request()

    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    now = datetime.now(UTC)
    args = dict(body.args)
    try:
        async with _source(request).connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, user_id, thread_id)
            # 이 턴의 어시스턴트 메시지는 아직 적재 전이라 어느 메시지에 매인지 확정할 수 없다.
            pending = await ledger.insert_pending_tool(
                generate_ulid(now), thread_id, body.toolName, args, now
            )
            await _announce(request, ledger, thread_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    return ok(
        {
            "confirmationId": pending["id"],
            "toolName": pending["tool_name"],
            "status": pending["status"],
            "summary": _summarize(body.toolName, args),
            "note": PROPOSAL_NOTE,
        },
        status=CREATED_STATUS,
    )


async def decide_chat_tool(thread_id: str, confirmation_id: str, request: Request) -> JSONResponse:
    """대기 중인 도구 호출 하나를 승인이나 거절로 해소한다."""
    body = await read_payload(request, DecideToolBody)
    if isinstance(body, JSONResponse):
        return body
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    try:
        async with _source(request).connect() as sql:
            return await _resolve(ChatSurfaceLedger(sql), request, user_id, thread_id, confirmation_id, body)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    except ChatToolArgsInvalid:
        return invalid_request()
    except ChatToolFailed:
        return rejection(ChatIntakeRejected(*TOOL_UNAVAILABLE))


async def _resolve(
    ledger: ChatSurfaceLedger,
    request: Request,
    user_id: str,
    thread_id: str,
    confirmation_id: str,
    body: DecideToolBody,
) -> JSONResponse:
    executor: ChatToolExecutor = request.app.state.chat_tool_executor
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
    await ledger.insert_tool_message(generate_ulid(now), thread_id, content, confirmation_id, now)
    await _announce(request, ledger, thread_id)
    return ok(
        {
            "confirmationId": confirmation_id,
            "toolName": tool_name,
            "status": resolved["status"],
            "result": content,
        }
    )


async def _pending(ledger: ChatSurfaceLedger, thread_id: str, confirmation_id: str) -> dict[str, Any]:
    pending = await ledger.find_pending_tool(confirmation_id)
    # 남의 스레드에 걸린 확인은 존재 자체를 알리지 않는다.
    if pending is None or pending["thread_id"] != thread_id:
        raise ChatIntakeRejected(*CONFIRMATION_NOT_FOUND)
    if pending["status"] != "pending":
        raise ChatIntakeRejected(*CONFIRMATION_RESOLVED)
    return pending


async def _announce(request: Request, ledger: ChatSurfaceLedger, thread_id: str) -> None:
    """확인 대기는 스레드 것이므로 지금 열려 있는 실행 채널에 실어 다른 연결이 그것을 본다."""
    updates: UpdateSignal | None = getattr(request.app.state, "execution_updates", None)
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


def _source(request: Request) -> SqlSource:
    source: SqlSource = request.app.state.execution_sql
    return source
