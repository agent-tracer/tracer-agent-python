"""브라우저의 잡 접수와 취소 요청을 tracer-api와 같은 경로와 봉투와 오류 형식으로 받는다."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio.exceptions import ApplicationError

from ..agents.runtime.ledger import SqlSource
from .jobs_dispatch import TemporalJobDispatch
from .jobs_kinds import AGENT_KIND_BY_WIRE, JOB_EXECUTOR
from .jobs_ledger import JobLedger
from .jobs_spec import AgentJobKind

JOBS_PATH = "/api/v1/jobs"
JOB_CANCEL_PATH = f"{JOBS_PATH}/{{execution_id}}/cancel"
ACCEPTED_STATUS = 202
MONITOR_USER_HEADER = "x-monitor-user"
DEFAULT_USER_ID = "local"
INVALID_REQUEST = (400, "validation_error", "Invalid request")
UNSUPPORTED_KIND = (400, "job.kind-not-supported", "This backend does not accept this job kind directly yet")
NOT_FOUND = (404, "not_found", "Job execution not found")
ENVELOPE_UNAVAILABLE = (502, "job.envelope-unavailable", "Could not obtain model and credential envelope")

# 세 잡 모두 이 창구가 직접 받으며, 문맥과 후보 배치는 워커가 실행 액티비티에서 스스로 조립한다.
_DIRECTLY_SUPPORTED_KINDS: frozenset[AgentJobKind] = frozenset(
    {"recipe-scan", "title-suggestion", "task-cleanup"}
)

_RECIPE_SCAN_DEADLINE_MS = 720_000
_TITLE_SUGGESTION_DEADLINE_MS = 180_000
_TASK_CLEANUP_DEADLINE_MS = 300_000
_DEFAULT_MAX_SUGGESTIONS = 20
_MAX_SUGGESTIONS_CAP = 50


class RecipeScanJobInput(BaseModel):
    """recipe-scan 접수가 받는 도메인 입력이며 태스크 소유권은 워커의 도구가 다시 검증한다."""

    model_config = ConfigDict(extra="forbid")

    taskId: str = Field(min_length=1, max_length=64)
    userPrompt: str | None = Field(default=None, min_length=1, max_length=4000)
    language: str | None = Field(default=None, min_length=1, max_length=16)
    trigger: Literal["dashboard", "session"] | None = None


class TitleSuggestionJobInput(BaseModel):
    """title-suggestion 접수가 받는 도메인 입력이며 대화 컨텍스트는 워커가 직접 조립한다."""

    model_config = ConfigDict(extra="forbid")

    taskId: str = Field(min_length=1, max_length=64)


class TaskCleanupFilters(BaseModel):
    """task-cleanup 접수가 받는 선택적 조정치다."""

    model_config = ConfigDict(extra="forbid")

    maxSuggestions: int | None = Field(default=None, ge=1, le=_MAX_SUGGESTIONS_CAP)


class TaskCleanupJobInput(BaseModel):
    """task-cleanup 접수가 받는 도메인 입력이며 후보 배치는 워커가 직접 조립한다."""

    model_config = ConfigDict(extra="forbid")

    filters: TaskCleanupFilters = Field(default_factory=TaskCleanupFilters)


# 잡 종류마다 다른 접수 입력 모델이며 워커가 스스로 채우는 문맥·후보 배치는 여기 싣지 않는다.
_INPUT_MODEL_BY_KIND: dict[AgentJobKind, type[BaseModel]] = {
    "recipe-scan": RecipeScanJobInput,
    "title-suggestion": TitleSuggestionJobInput,
    "task-cleanup": TaskCleanupJobInput,
}


def _task_id(job_input: BaseModel) -> str | None:
    """태스크에 매인 잡 종류만 원장 조회의 taskId 컬럼에 실을 값을 갖는다."""
    if isinstance(job_input, RecipeScanJobInput | TitleSuggestionJobInput):
        return job_input.taskId
    return None


def _build_payload(
    job_input: BaseModel,
    user_id: str,
    execution_id: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """잡 종류에 맞는 액티비티 입력을 지으며 문맥과 후보 배치는 워커 액티비티가 스스로 채운다."""
    base = {
        "userId": user_id,
        "executionId": execution_id,
        "idempotencyKey": idempotency_key,
    }
    if isinstance(job_input, RecipeScanJobInput):
        return {
            **base,
            "taskId": job_input.taskId,
            "language": job_input.language or "auto",
            "userPrompt": job_input.userPrompt,
            "deadlineMs": _RECIPE_SCAN_DEADLINE_MS,
        }
    if isinstance(job_input, TitleSuggestionJobInput):
        return {
            **base,
            "taskId": job_input.taskId,
            "language": "auto",
            "deadlineMs": _TITLE_SUGGESTION_DEADLINE_MS,
        }
    assert isinstance(job_input, TaskCleanupJobInput)
    return {
        **base,
        "language": "auto",
        "deadlineMs": _TASK_CLEANUP_DEADLINE_MS,
        "maxSuggestions": job_input.filters.maxSuggestions or _DEFAULT_MAX_SUGGESTIONS,
    }


class JobEnqueueBody(BaseModel):
    """tracer-api의 잡 접수 본문과 같은 모양이며 브라우저는 백엔드마다 본문을 가르지 않는다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["title.suggestion", "recipe.scan", "task.cleanup"]
    input: dict[str, Any] = Field(default_factory=dict)
    idempotencyKey: str | None = Field(default=None, min_length=1, max_length=200)


async def enqueue_job(request: Request) -> JSONResponse:
    """잡 하나를 접수하고 결과나 사유를 tracer-api와 같은 봉투로 낸다."""
    body = await _read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        enqueue = JobEnqueueBody.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=_details(invalid))

    kind = AGENT_KIND_BY_WIRE[enqueue.kind]
    if kind not in _DIRECTLY_SUPPORTED_KINDS:
        return error_envelope(*UNSUPPORTED_KIND)
    try:
        job_input = _INPUT_MODEL_BY_KIND[kind].model_validate(enqueue.input)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=_details(invalid))

    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    execution_id = enqueue.idempotencyKey or str(uuid.uuid4())
    now = datetime.now(UTC)

    envelopes = request.app.state.job_envelopes
    try:
        # 접수는 이 사용자의 자격과 카탈로그가 실제로 발급되는지를 봉투로 확인한다.
        await envelopes.issue(enqueue.kind, user_id)
    except ApplicationError as unavailable:
        return error_envelope(*ENVELOPE_UNAVAILABLE, details=str(unavailable))
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        await JobLedger(sql).claim(
            execution_id,
            user_id,
            enqueue.kind,
            JOB_EXECUTOR,
            _task_id(job_input),
            enqueue.idempotencyKey,
            enqueue.input,
            now,
        )

    dispatch: TemporalJobDispatch = request.app.state.job_dispatch
    payload = _build_payload(job_input, user_id, execution_id, enqueue.idempotencyKey)
    await dispatch.start(kind, execution_id, payload)

    return JSONResponse(
        status_code=ACCEPTED_STATUS,
        content={"ok": True, "data": {"runId": execution_id, "executionId": execution_id}},
    )


async def cancel_job(execution_id: str, request: Request) -> JSONResponse:
    """도는 잡 실행 하나를 끊고 결과나 사유를 tracer-api와 같은 봉투로 낸다."""
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        row = await JobLedger(sql).find(execution_id)
    if row is None:
        return error_envelope(*NOT_FOUND)

    dispatch: TemporalJobDispatch = request.app.state.job_dispatch
    cancelled = await dispatch.cancel(AGENT_KIND_BY_WIRE[str(row["kind"])], execution_id)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"cancelled": cancelled}})


def resolve_user_id(header: str | None) -> str:
    """자기신고 사용자 헤더가 비면 tracer-api와 같은 기본 사용자로 읽는다."""
    trimmed = (header or "").strip()
    return trimmed if trimmed else DEFAULT_USER_ID


def error_envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    """실패 사유를 tracer-api의 전역 필터와 같은 오류 봉투로 적는다."""
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
