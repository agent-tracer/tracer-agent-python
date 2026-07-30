"""실행기가 보낸 누적 답변을 원장에 반영하고 열린 연결을 깨운다."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse

from ...runtime.ledger import SqlSource
from ..execution_ledger import ChatExecutionLedger
from ..intake.cancel import UpdateSignal
from ..intake.router import error_envelope
from ..models import TERMINAL_CHAT_EXECUTION_STATUSES
from .envelope import ok, read_payload
from .models import DraftCheckpointBody

CHAT_DRAFTS_PATH = "/api/v1/chat/executions/{execution_id}/drafts"

EXECUTION_NOT_FOUND = (404, "not_found", "Chat execution not found")
TOKEN_REJECTED = (403, "forbidden", "Chat draft callback is not authorized")


async def checkpoint_chat_draft(execution_id: str, request: Request) -> JSONResponse:
    """지금까지 만든 누적 답변을 통지받아 살아 있는 시도의 것일 때만 원장에 적는다."""
    body = await read_payload(request, DraftCheckpointBody)
    if isinstance(body, JSONResponse):
        return body

    source: SqlSource = request.app.state.execution_sql
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
            execution_id, int(execution["attempt"]), body.text, body.draftSeq, datetime.now(UTC)
        )

    if stored:
        await _wake(request, execution_id)
    # 취소 등록이 다른 인스턴스에 닿지 않으므로 이 응답이 종결을 대신 알린다.
    return ok({"stored": stored, "terminal": execution["status"] in TERMINAL_CHAT_EXECUTION_STATUSES})


def _accepts(token_hash: object, token: str) -> bool:
    if token_hash is None:
        return False
    return str(token_hash) == hashlib.sha256(token.encode()).hexdigest()


async def _wake(request: Request, execution_id: str) -> None:
    updates: UpdateSignal | None = getattr(request.app.state, "execution_updates", None)
    if updates is not None:
        await updates.publish(execution_id, {"executionId": execution_id})
