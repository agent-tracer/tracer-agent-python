"""배포 단위 사이에서만 오가는 프롬프트 등록 요청을 계약이 정한 봉투로 받는다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..runtime.ledger import SqlSource
from .fragments import PromptFragmentRegistration
from .models import RegisterAndResolveFragmentsPayload, RegisterPromptPayload
from .prompts import PromptRegistration

PROMPT_FRAGMENTS_REGISTER_PATH = "/internal/prompts/fragments/register-and-resolve"
PROMPT_REGISTER_PATH = "/internal/prompts/python/register"
CREATED_STATUS = 201
MONITOR_USER_HEADER = "x-monitor-user"
DEFAULT_USER_ID = "local"
INVALID_REQUEST = (400, "validation_error", "Invalid request")


async def register_and_resolve_prompt_fragments(request: Request) -> JSONResponse:
    """올라온 조각 묶음을 원장에 세우고 이번 실행이 쓸 조각을 성공 봉투로 낸다."""
    body = await _read_body(request)
    if body is None:
        return _error_envelope(*INVALID_REQUEST)
    try:
        payload = RegisterAndResolveFragmentsPayload.model_validate(body)
    except ValidationError as invalid:
        return _error_envelope(*INVALID_REQUEST, details=_details(invalid))

    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        resolved = await PromptFragmentRegistration(sql).register_and_resolve(payload, datetime.now(UTC))
    return JSONResponse(status_code=200, content={"ok": True, "data": resolved})


async def register_prompt(request: Request) -> JSONResponse:
    """올라온 프롬프트 정의와 판을 원장에 세우고 그 결과를 성공 봉투로 낸다."""
    body = await _read_body(request)
    if body is None:
        return _error_envelope(*INVALID_REQUEST)
    try:
        payload = RegisterPromptPayload.model_validate(body)
    except ValidationError as invalid:
        return _error_envelope(*INVALID_REQUEST, details=_details(invalid))

    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        registered = await PromptRegistration(sql).register(
            _resolve_user_id(request.headers.get(MONITOR_USER_HEADER)), payload, datetime.now(UTC)
        )
    return JSONResponse(status_code=CREATED_STATUS, content={"ok": True, "data": registered})


def _resolve_user_id(header: str | None) -> str:
    trimmed = (header or "").strip()
    return trimmed if trimmed else DEFAULT_USER_ID


def _error_envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
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
