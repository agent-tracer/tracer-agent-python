"""배포 단위 사이에서만 오가는 실행 봉투 요청을 계약이 정한 경로와 봉투로 받는다."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..runtime.dependencies import ExecutionSql
from ..shared.wire import SuccessEnvelope, error_envelope, error_responses, read_body, validation_details
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

router = APIRouter()


def get_model_credentials(request: Request) -> ModelCredentialSource:
    """앱 수명이 세운 사용자별 모델 자격의 출처를 낸다."""
    credentials: ModelCredentialSource = request.app.state.model_credentials
    return credentials


def get_read_api_base_url(request: Request) -> str:
    """실행기가 추적 자료를 읽을 때 칠 주소를 낸다."""
    base_url: str = request.app.state.read_api_base_url
    return base_url


def get_agent_api_base_url(request: Request) -> str:
    """실행기가 이 서비스로 되돌아올 때 칠 주소를 낸다."""
    base_url: str = request.app.state.agent_api_base_url
    return base_url


ModelCredentials = Annotated[ModelCredentialSource, Depends(get_model_credentials)]
ReadApiBaseUrl = Annotated[str, Depends(get_read_api_base_url)]
AgentApiBaseUrl = Annotated[str, Depends(get_agent_api_base_url)]


@router.post(CHAT_ENVELOPE_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404))
async def issue_chat_execution_envelope(
    execution_id: str,
    source: ExecutionSql,
    credentials: ModelCredentials,
    read_api_base_url: ReadApiBaseUrl,
    agent_api_base_url: AgentApiBaseUrl,
) -> JSONResponse:
    """이번 대화 실행 시도가 쓸 모델과 자격과 단가와 한도를 낸다."""
    async with source.connect() as sql:
        rows = await sql.fetch(_SELECT_EXECUTION, execution_id)
    if not rows:
        return error_envelope(*EXECUTION_NOT_FOUND)

    row = rows[0]
    api_key = await _api_key(credentials, str(row["user_id"]))
    if api_key is None:
        return error_envelope(*CHAT_KEY_MISSING)

    data = chat_envelope(
        execution_id=execution_id,
        model=None if row["model"] is None else str(row["model"]),
        api_key=api_key,
        catalog=CATALOG[CHAT_KIND],
        read_api_base_url=read_api_base_url,
        agent_api_base_url=agent_api_base_url,
        grant=issue_draft_grant(),
        user_id=str(row["user_id"]),
        now_ms=int(time.time() * 1000),
    )
    return JSONResponse(status_code=200, content={"ok": True, "data": data})


@router.post(JOB_ENVELOPE_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def issue_job_execution_envelope(
    kind: str, request: Request, credentials: ModelCredentials
) -> JSONResponse:
    """이 잡 종류와 사용자가 쓸 모델과 자격과 단가와 한도를 낸다."""
    if kind not in JOB_KINDS:
        return error_envelope(*INVALID_REQUEST)
    body = await read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        payload = JobEnvelopeBody.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))

    api_key = await _api_key(credentials, payload.userId)
    if api_key is None:
        return error_envelope(*JOB_KEY_MISSING)

    data = job_envelope(api_key=api_key, catalog=CATALOG[kind])
    return JSONResponse(status_code=200, content={"ok": True, "data": data})


async def _api_key(credentials: ModelCredentialSource, user_id: str) -> str | None:
    stored = await credentials.api_key(user_id)
    return stored if stored else None
