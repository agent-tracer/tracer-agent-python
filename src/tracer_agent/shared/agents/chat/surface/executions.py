"""대화 실행의 이력과 궤적과 되읽기를 계약이 정한 경로로 낸다."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...runtime.dependencies import ExecutionSql, UserId
from ...shared.wire import SuccessEnvelope, error_responses, ok
from ..intake.turn import ChatIntakeRejected
from .access import owned_execution, owned_thread
from .envelope import rejection
from .ledger import PENDING, ChatSurfaceLedger
from .replay import ChatReplayMessageMissing, build_chat_replay
from .threads import CHAT_THREAD_PATH
from .views import confirmation_dto, execution_dto, step_dto

CHAT_EXECUTIONS_PATH = f"{CHAT_THREAD_PATH}/executions"
CHAT_EXECUTION_PATH = f"{CHAT_EXECUTIONS_PATH}/{{execution_id}}"
CHAT_EXECUTION_STEPS_PATH = f"{CHAT_EXECUTION_PATH}/steps"
CHAT_EXECUTION_REPLAY_PATH = f"{CHAT_EXECUTION_PATH}/replay"

REPLAY_UNBUILDABLE = (404, "not_found", "Chat replay message not found")

router = APIRouter()


@router.get(CHAT_EXECUTIONS_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def list_chat_executions(thread_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """스레드의 실행 이력과 지금 승인을 기다리는 도구를 함께 낸다."""
    try:
        async with source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, user_id, thread_id)
            executions = await ledger.list_executions(thread_id)
            pending = await ledger.list_pending_tools(thread_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    return ok(
        {
            "items": [execution_dto(row) for row in executions],
            "confirmations": [confirmation_dto(row) for row in pending if row["status"] == PENDING],
        }
    )


@router.get(CHAT_EXECUTION_STEPS_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def list_chat_execution_steps(
    thread_id: str, execution_id: str, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """대화 턴 하나가 남긴 궤적을 시도와 순번의 오름차순으로 낸다."""
    try:
        async with source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_execution(ledger, user_id, thread_id, execution_id)
            steps = await ledger.list_steps(execution_id, user_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)
    return ok({"items": [step_dto(row) for row in steps]})


@router.get(CHAT_EXECUTION_REPLAY_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def get_chat_replay(
    thread_id: str, execution_id: str, source: ExecutionSql, user_id: UserId
) -> JSONResponse:
    """이번 턴에 모델에게 되돌려 줄 대화 이력과 요약과 기억을 낸다."""
    try:
        async with source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            execution = await owned_execution(ledger, user_id, thread_id, execution_id)
            thread = await owned_thread(ledger, user_id, thread_id)
            # 승인이 적재한 도구 결과 줄은 어느 실행에도 매이지 않으므로 실행으로 거르지 않는다.
            messages = await ledger.list_messages(thread_id)
            memories = await ledger.list_memories(user_id)
    except ChatIntakeRejected as rejected:
        return rejection(rejected)

    summary = None if thread["summary"] is None else str(thread["summary"])
    try:
        replayed = build_chat_replay(messages, str(execution["user_message_id"]), summary)
    except ChatReplayMessageMissing:
        return rejection(ChatIntakeRejected(*REPLAY_UNBUILDABLE))
    return ok(
        {
            "messages": replayed,
            "summary": summary,
            "facts": [{"key": row["key"], "content": row["content"]} for row in memories],
        }
    )
