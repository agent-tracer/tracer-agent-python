"""브라우저의 잡 접수와 취소 요청을 계약이 정한 경로와 봉투와 오류 형식으로 받는다."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from temporalio.exceptions import ApplicationError

from ..agents.runtime.dependencies import ExecutionSql, LeaseOwner, UserId
from ..agents.runtime.ledger import SqlSource
from ..agents.shared.wire import SuccessEnvelope, error_responses
from .jobs_anchor import RuleAnchorSource
from .jobs_dispatch import TemporalJobDispatch
from .jobs_envelope import JobEnvelopeSource
from .jobs_input import (
    INPUT_MODEL_BY_KIND,
    RuleGenerationJobInput,
    build_payload,
    input_hash,
    task_id_of,
)
from .jobs_kinds import AGENT_KIND_BY_WIRE, JOB_EXECUTOR, lease_ttl_ms, runs_locally
from .jobs_ledger import JobLedger
from .jobs_view import iso, job_dto

JOBS_PATH = "/api/agent/jobs"
JOB_CANCEL_PATH = f"{JOBS_PATH}/{{execution_id}}/cancel"
JOB_START_PATH = f"{JOBS_PATH}/{{execution_id}}/start"
JOB_LEASE_PATH = f"{JOBS_PATH}/{{execution_id}}/lease"
JOB_RESULTS_PATH = f"{JOBS_PATH}/{{execution_id}}/results"
JOB_FAIL_PATH = f"{JOBS_PATH}/{{execution_id}}/fail"
JOB_RELEASE_PATH = f"{JOBS_PATH}/{{execution_id}}/release"
ACCEPTED_STATUS = 202
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
LEASE_HELD = (409, "job.lease-held", "Job lease is held by another runner")


class JobEnqueueBody(BaseModel):
    """계약이 정한 잡 접수 본문이며 브라우저는 백엔드마다 본문을 가르지 않는다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["title.suggestion", "recipe.scan", "task.cleanup", "rule.generation"]
    input: dict[str, Any] = Field(default_factory=dict)
    idempotencyKey: str | None = Field(default=None, min_length=1, max_length=200)


router = APIRouter()


def get_rule_anchors(request: Request) -> RuleAnchorSource:
    """규칙 생성의 근거를 추적 창구에서 읽는 통로를 낸다."""
    anchors: RuleAnchorSource = request.app.state.rule_anchors
    return anchors


def get_job_envelopes(request: Request) -> JobEnvelopeSource:
    """잡 실행 시도가 쓸 봉투를 발급받는 통로를 낸다."""
    envelopes: JobEnvelopeSource = request.app.state.job_envelopes
    return envelopes


def get_job_dispatch(request: Request) -> TemporalJobDispatch:
    """잡 실행을 워커에게 맡기는 통로를 낸다."""
    dispatch: TemporalJobDispatch = request.app.state.job_dispatch
    return dispatch


RuleAnchors = Annotated[RuleAnchorSource, Depends(get_rule_anchors)]
JobEnvelopes = Annotated[JobEnvelopeSource, Depends(get_job_envelopes)]
JobDispatch = Annotated[TemporalJobDispatch, Depends(get_job_dispatch)]


@router.post(
    JOBS_PATH,
    status_code=ACCEPTED_STATUS,
    response_model=SuccessEnvelope,
    responses=error_responses(400, 404, 409, 502),
)
async def enqueue_job(
    request: Request,
    source: ExecutionSql,
    user_id: UserId,
    anchors: RuleAnchors,
    envelopes: JobEnvelopes,
    dispatch: JobDispatch,
) -> JSONResponse:
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

    if isinstance(job_input, RuleGenerationJobInput) and not await _owns_anchor(anchors, user_id, job_input):
        return error_envelope(*INVALID_RULE_ANCHOR)
    idempotency_key = _idempotency_key(enqueue.idempotencyKey)
    request_hash = None if idempotency_key is None else input_hash(enqueue.kind, job_input)
    now = datetime.now(UTC)

    if not runs_locally(enqueue.kind):
        try:
            # 접수는 이 사용자의 자격과 카탈로그가 실제로 발급되는지를 봉투로 확인한다.
            await envelopes.issue(enqueue.kind, user_id)
        except ApplicationError as unavailable:
            return error_envelope(*ENVELOPE_UNAVAILABLE, details=str(unavailable))
    execution_id = str(uuid.uuid4())
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        created = await ledger.claim(
            execution_id,
            user_id,
            enqueue.kind,
            JOB_EXECUTOR[enqueue.kind],
            task_id_of(job_input),
            idempotency_key,
            request_hash,
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
    if not created and row["idempotency_input_hash"] != request_hash:
        return error_envelope(*IDEMPOTENCY_CONFLICT)

    job_id = str(row["id"])
    if not runs_locally(enqueue.kind) and (created or row["status"] == "pending"):
        payload = build_payload(job_input, user_id, job_id, idempotency_key)
        await dispatch.start(AGENT_KIND_BY_WIRE[enqueue.kind], job_id, payload)

    return JSONResponse(status_code=ACCEPTED_STATUS, content={"ok": True, "data": {"job": job_dto(row)}})


@router.post(JOB_CANCEL_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def cancel_job(
    execution_id: str, source: ExecutionSql, user_id: UserId, dispatch: JobDispatch
) -> JSONResponse:
    """도는 잡 하나를 끊고 원장 행이나 사유를 계약이 정한 봉투로 낸다."""
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
        await dispatch.cancel(AGENT_KIND_BY_WIRE[kind], execution_id)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"job": job_dto(row)}})


async def _owns_anchor(anchors: RuleAnchorSource, user_id: str, job_input: RuleGenerationJobInput) -> bool:
    """규칙 생성의 근거는 이 사용자의 그 태스크에 속한 사용자 발화여야 한다."""
    anchor = await anchors.find(user_id, job_input.anchorEventId)
    return anchor is not None and anchor.task_id == job_input.taskId and anchor.user_message


def _idempotency_key(value: str | None) -> str | None:
    """공백뿐인 멱등키는 키를 싣지 않은 것과 같게 본다."""
    trimmed = (value or "").strip()
    return trimmed or None


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


class JobReportBody(BaseModel):
    """실행기가 만든 규칙과 그 근거이며 잡의 산출물 자리에 그대로 실린다."""

    model_config = ConfigDict(extra="forbid")

    rules: list[dict[str, Any]]
    skipped: list[str] | None = None


class JobFailureBody(BaseModel):
    """실행기가 보고하는 실패이며 원장의 오류 자리에 남는다."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    retryable: bool = False


@router.post(JOB_START_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def claim_job(
    execution_id: str, source: ExecutionSql, user_id: UserId, owner: LeaseOwner
) -> JSONResponse:
    """대기 중인 잡 하나를 부른 실행기의 리스로 가져간다."""
    now = datetime.now(UTC)
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        expires_at = now + timedelta(milliseconds=lease_ttl_ms())
        if not await ledger.claim_lease(execution_id, owner, expires_at, now):
            return error_envelope(*LEASE_HELD)
    return JSONResponse(status_code=200, content={"ok": True, "data": _lease(owner, expires_at)})


@router.post(JOB_LEASE_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def renew_job_lease(
    execution_id: str, source: ExecutionSql, user_id: UserId, owner: LeaseOwner
) -> JSONResponse:
    """실행이 길어지는 동안 쥔 리스의 수명을 늘린다."""
    now = datetime.now(UTC)
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        expires_at = now + timedelta(milliseconds=lease_ttl_ms())
        if not await ledger.renew_lease(execution_id, owner, expires_at, now):
            held = _lease_of(row, owner, now)
            return JSONResponse(status_code=200, content={"ok": True, "data": held})
    return JSONResponse(status_code=200, content={"ok": True, "data": _lease(owner, expires_at)})


@router.post(JOB_RESULTS_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def report_job_result(
    execution_id: str,
    body: JobReportBody,
    source: ExecutionSql,
    user_id: UserId,
    owner: LeaseOwner,
) -> JSONResponse:
    """실행기가 만든 산출물을 잡에 싣고 종결한다."""
    return await _settle(
        execution_id, source, user_id, owner, "completed", body.model_dump(exclude_none=True), None
    )


@router.post(JOB_FAIL_PATH, response_model=SuccessEnvelope, responses=error_responses(404, 409))
async def fail_job(
    execution_id: str,
    body: JobFailureBody,
    source: ExecutionSql,
    user_id: UserId,
    owner: LeaseOwner,
) -> JSONResponse:
    """실행기가 실패를 보고하고 잡을 종결한다."""
    return await _settle(execution_id, source, user_id, owner, "failed", {}, body.message)


@router.post(JOB_RELEASE_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def release_job(
    execution_id: str, source: ExecutionSql, user_id: UserId, owner: LeaseOwner
) -> JSONResponse:
    """끝내지 못한 실행기가 리스를 놓아 잡을 곧바로 대기로 돌린다."""
    now = datetime.now(UTC)
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        released = await ledger.release_lease(execution_id, owner, now)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"released": released}})


async def _settle(
    execution_id: str,
    source: SqlSource,
    user_id: str,
    owner: str,
    status: str,
    result: dict[str, Any],
    error: str | None,
) -> JSONResponse:
    now = datetime.now(UTC)
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        if not await ledger.settle_with_lease(execution_id, owner, status, result, error, now):
            return error_envelope(*LEASE_HELD)
        row = await ledger.find(execution_id) or row
    return JSONResponse(status_code=200, content={"ok": True, "data": {"job": job_dto(row)}})


def _lease(owner: str, expires_at: datetime) -> dict[str, Any]:
    return {"held": True, "leaseOwner": owner, "leaseExpiresAt": iso(expires_at)}


def _lease_of(row: Mapping[str, Any], owner: str, now: datetime) -> dict[str, Any]:
    """쥔 사람이 부른 사람과 같고 아직 살아 있을 때만 쥔 것으로 본다."""
    held_by = row["lease_owner"]
    expires_at = row["lease_expires_at"]
    alive = expires_at is not None and expires_at > now
    return {
        "held": bool(alive and held_by == owner),
        "leaseOwner": held_by,
        "leaseExpiresAt": None if expires_at is None else iso(expires_at),
    }
