"""배포 단위 사이에서만 오가는 실행 봉투 요청을 계약이 정한 경로와 봉투로 받는다."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..runtime.ledger import SqlSource
from .catalog import CATALOG, CHAT_KIND, JOB_KINDS
from .grants import issue_draft_grant
from .issue import chat_envelope, job_envelope
from .models import JobEnvelopeBody, ModelCredentialSource

CHAT_ENVELOPE_PATH = "/internal/chat/executions/{execution_id}/envelope"
JOB_ENVELOPE_PATH = "/internal/jobs/{kind}/envelope"
INVALID_REQUEST = (400, "validation_error", "Invalid request")
EXECUTION_NOT_FOUND = (404, "not_found", "Chat execution not found")
CHAT_KEY_MISSING = (400, "chat.llm-key-missing", "Model credential is not configured")
JOB_KEY_MISSING = (400, "job.llm-key-missing", "Model credential is not configured")

_SELECT_EXECUTION = "SELECT user_id, model FROM chat_executions WHERE id = $1"


async def issue_chat_execution_envelope(execution_id: str, request: Request) -> JSONResponse:
    """이번 대화 실행 시도가 쓸 모델과 자격과 단가와 한도를 낸다."""
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        rows = await sql.fetch(_SELECT_EXECUTION, execution_id)
    if not rows:
        return error_envelope(*EXECUTION_NOT_FOUND)

    row = rows[0]
    api_key = await _api_key(request, str(row["user_id"]))
    if api_key is None:
        return error_envelope(*CHAT_KEY_MISSING)

    data = chat_envelope(
        execution_id=execution_id,
        model=None if row["model"] is None else str(row["model"]),
        api_key=api_key,
        catalog=CATALOG[CHAT_KIND],
        read_api_base_url=request.app.state.read_api_base_url,
        agent_api_base_url=request.app.state.agent_api_base_url,
        grant=issue_draft_grant(),
    )
    return JSONResponse(status_code=200, content={"ok": True, "data": data})


async def issue_job_execution_envelope(kind: str, request: Request) -> JSONResponse:
    """이 잡 종류와 사용자가 쓸 모델과 자격과 단가와 한도를 낸다."""
    if kind not in JOB_KINDS:
        return error_envelope(*INVALID_REQUEST)
    body = await _read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        payload = JobEnvelopeBody.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=_details(invalid))

    api_key = await _api_key(request, payload.userId)
    if api_key is None:
        return error_envelope(*JOB_KEY_MISSING)

    data = job_envelope(api_key=api_key, catalog=CATALOG[kind])
    return JSONResponse(status_code=200, content={"ok": True, "data": data})


async def _api_key(request: Request, user_id: str) -> str | None:
    credentials: ModelCredentialSource = request.app.state.model_credentials
    stored = await credentials.api_key(user_id)
    return stored if stored else None


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
