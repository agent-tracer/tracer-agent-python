"""실행기가 보낸 누적 답변을 원장에 반영하고 열린 연결에 알린다."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...runtime.dependencies import ExecutionSql
from ...shared.wire import SuccessEnvelope, error_envelope, error_responses, ok
from ..dependencies import Updates, Watch
from ..execution_ledger import ChatExecutionLedger
from ..intake.cancel import UpdateSignal
from ..models import TERMINAL_CHAT_EXECUTION_STATUSES
from ..rejections import EXECUTION_NOT_FOUND
from .envelope import read_payload
from .models import DraftCheckpointBody
from .updates import ChatExecutionUpdates

CHAT_DRAFTS_PATH = "/api/agent/chat/executions/{execution_id}/drafts"

TOKEN_REJECTED = (403, "forbidden", "Chat draft callback is not authorized")

router = APIRouter()


@router.post(CHAT_DRAFTS_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 403, 404))
async def checkpoint_chat_draft(
    execution_id: str, request: Request, source: ExecutionSql, updates: Updates, watch: Watch
) -> JSONResponse:
    """지금까지 만든 누적 답변을 통지받아 살아 있는 시도의 것일 때만 원장에 적는다."""
    body = await read_payload(request, DraftCheckpointBody)
    if isinstance(body, JSONResponse):
        return body

    async with source.connect() as sql:
        ledger = ChatExecutionLedger(sql)
        execution = await ledger.find_by_id(execution_id)
        # 토큰은 사용자 세션을 대신하지 않으므로 실행의 존재조차 자격이 맞을 때만 드러낸다.
        if execution is None:
            return error_envelope(*EXECUTION_NOT_FOUND)
        if not _accepts(execution["draft_token_hash"], body.token):
            return error_envelope(*TOKEN_REJECTED)
        # 재시도가 붙인 시도 번호를 실행기가 알 길이 없으므로 살아 있는 시도는 원장이 정한다.
        stored = await ledger.checkpoint_running(
            execution_id,
            int(execution["attempt"]),
            body.text,
            body.draftSeq,
            body.phase,
            datetime.now(UTC),
        )

    if stored:
        await _wake(updates, watch, execution_id)
    # 취소 등록이 다른 인스턴스에 닿지 않으므로 이 응답이 종결을 대신 알린다.
    return ok({"stored": stored, "terminal": execution["status"] in TERMINAL_CHAT_EXECUTION_STATUSES})


def _accepts(token_hash: object, token: str) -> bool:
    if token_hash is None:
        return False
    return str(token_hash) == hashlib.sha256(token.encode()).hexdigest()


async def _wake(updates: UpdateSignal | None, watch: ChatExecutionUpdates | None, execution_id: str) -> None:
    """이 프로세스의 연결에 먼저 알리고 다른 replica 에는 브로커로 알린다."""
    if watch is not None:
        watch.notify(execution_id)
    if updates is not None:
        await updates.publish(execution_id, {"executionId": execution_id})
