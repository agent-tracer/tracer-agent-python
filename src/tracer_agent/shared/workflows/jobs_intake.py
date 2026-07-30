"""브라우저의 잡 접수와 취소 요청을 계약이 정한 경로와 봉투와 오류 형식으로 받는다."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio.exceptions import ApplicationError

from ..agents.runtime.ledger import SqlSource
from .jobs_anchor import RuleAnchorSource
from .jobs_dispatch import TemporalJobDispatch
from .jobs_input import INPUT_MODEL_BY_KIND, RuleGenerationJobInput, build_payload, task_id_of
from .jobs_kinds import AGENT_KIND_BY_WIRE, JOB_EXECUTOR, runs_locally
from .jobs_ledger import JobLedger
from .jobs_view import job_dto

JOBS_PATH = "/api/agent/jobs"
JOB_CANCEL_PATH = f"{JOBS_PATH}/{{execution_id}}/cancel"
ACCEPTED_STATUS = 202
MONITOR_USER_HEADER = "x-monitor-user"
DEFAULT_USER_ID = "local"
INVALID_REQUEST = (400, "validation_error", "Invalid request")
INVALID_RULE_ANCHOR = (
    400,
    "job.invalid-rule-anchor",
    "Rule generation requires an owned user-message anchor",
)
NOT_FOUND = (404, "not_found", "Job execution not found")
IDEMPOTENCY_CONFLICT = (
    409,
    "job.idempotency-conflict",
    "Idempotency key was already used with different job input",
)
ENVELOPE_UNAVAILABLE = (502, "job.envelope-unavailable", "Could not obtain model and credential envelope")


class JobEnqueueBody(BaseModel):
    """계약이 정한 잡 접수 본문이며 브라우저는 백엔드마다 본문을 가르지 않는다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["title.suggestion", "recipe.scan", "task.cleanup", "rule.generation"]
    input: dict[str, Any] = Field(default_factory=dict)
    idempotencyKey: str | None = Field(default=None, min_length=1, max_length=200)


async def enqueue_job(request: Request) -> JSONResponse:
    """잡 하나를 접수하고 원장 행이나 사유를 계약이 정한 봉투로 낸다."""
    body = await _read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        enqueue = JobEnqueueBody.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=_details(invalid))

    try:
        job_input = INPUT_MODEL_BY_KIND[enqueue.kind].model_validate(enqueue.input)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=_details(invalid))

    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    if isinstance(job_input, RuleGenerationJobInput) and not await _owns_anchor(
        request.app.state.rule_anchors, user_id, job_input
    ):
        return error_envelope(*INVALID_RULE_ANCHOR)
    idempotency_key = _idempotency_key(enqueue.idempotencyKey)
    input_hash = None if idempotency_key is None else _input_hash(enqueue.input)
    now = datetime.now(UTC)

    if not runs_locally(enqueue.kind):
        envelopes = request.app.state.job_envelopes
        try:
            # 접수는 이 사용자의 자격과 카탈로그가 실제로 발급되는지를 봉투로 확인한다.
            await envelopes.issue(enqueue.kind, user_id)
        except ApplicationError as unavailable:
            return error_envelope(*ENVELOPE_UNAVAILABLE, details=str(unavailable))
    execution_id = str(uuid.uuid4())
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        created = await ledger.claim(
            execution_id,
            user_id,
            enqueue.kind,
            JOB_EXECUTOR[enqueue.kind],
            task_id_of(job_input),
            idempotency_key,
            input_hash,
            enqueue.input,
            now,
        )
        if created:
            row = await ledger.find(execution_id)
        elif idempotency_key is not None:
            row = await ledger.find_by_idempotency(user_id, enqueue.kind, idempotency_key)
        else:
            row = None
    if row is None:
        return error_envelope(*NOT_FOUND)
    if not created and row["idempotency_input_hash"] != input_hash:
        return error_envelope(*IDEMPOTENCY_CONFLICT)

    job_id = str(row["id"])
    if not runs_locally(enqueue.kind) and (created or row["status"] == "pending"):
        dispatch: TemporalJobDispatch = request.app.state.job_dispatch
        payload = build_payload(job_input, user_id, job_id, idempotency_key)
        await dispatch.start(AGENT_KIND_BY_WIRE[enqueue.kind], job_id, payload)

    return JSONResponse(status_code=ACCEPTED_STATUS, content={"ok": True, "data": {"job": job_dto(row)}})


async def cancel_job(execution_id: str, request: Request) -> JSONResponse:
    """도는 잡 하나를 끊고 원장 행이나 사유를 계약이 정한 봉투로 낸다."""
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        if await ledger.cancel(execution_id, datetime.now(UTC)):
            row = await ledger.find(execution_id) or row

    kind = str(row["kind"])
    # 전이를 먼저 하면 취소에 실패했을 때 취소됐다고 기록한 채 유료 실행이 이어진다.
    if not runs_locally(kind):
        dispatch: TemporalJobDispatch = request.app.state.job_dispatch
        await dispatch.cancel(AGENT_KIND_BY_WIRE[kind], execution_id)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"job": job_dto(row)}})


def resolve_user_id(header: str | None) -> str:
    """자기신고 사용자 헤더가 비면 계약이 정한 기본 사용자로 읽는다."""
    trimmed = (header or "").strip()
    return trimmed if trimmed else DEFAULT_USER_ID


async def _owns_anchor(anchors: RuleAnchorSource, user_id: str, job_input: RuleGenerationJobInput) -> bool:
    """규칙 생성의 근거는 이 사용자의 그 태스크에 속한 사용자 발화여야 한다."""
    anchor = await anchors.find(user_id, job_input.anchorEventId)
    return anchor is not None and anchor.task_id == job_input.taskId and anchor.user_message


def _idempotency_key(value: str | None) -> str | None:
    """공백뿐인 멱등키는 키를 싣지 않은 것과 같게 본다."""
    trimmed = (value or "").strip()
    return trimmed or None


def _input_hash(job_input: dict[str, Any]) -> str:
    """같은 멱등키로 다시 온 접수가 같은 입력인지를 가르는 안정 해시다."""
    encoded = json.dumps(job_input, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


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
