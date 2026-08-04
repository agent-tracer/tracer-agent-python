"""브라우저의 잡 접수와 취소 요청을 계약이 정한 경로와 봉투와 오류 형식으로 받는다."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..agents.envelope.models import ModelCredentialSource
from ..agents.envelope.router import JOB_KEY_MISSING
from ..agents.runtime.dependencies import ExecutionSql, LeaseOwner, UserId
from ..agents.runtime.ledger import SqlSource
from ..agents.shared.instant import opt_iso
from ..agents.shared.json_view import JsonObject
from ..agents.shared.models import AgentStepDTO
from ..agents.shared.wire import (
    SuccessEnvelope,
    error_envelope,
    error_responses,
    read_body,
    validation_details,
)
from .jobs_anchor import RuleAnchorSource, ScanAnchorSource
from .jobs_dispatch import TemporalJobDispatch
from .jobs_input import (
    INPUT_MODEL_BY_KIND,
    AdmissionContext,
    build_payload,
    input_hash,
    task_id_of,
)
from .jobs_kinds import JOB_EXECUTOR, AgentJobKind, lease_ttl_ms, runs_locally
from .jobs_ledger import JobLedger
from .jobs_view import job_dto

JOBS_PATH = "/api/agent/jobs"
JOB_CANCEL_PATH = f"{JOBS_PATH}/{{execution_id}}/cancel"
JOB_START_PATH = f"{JOBS_PATH}/{{execution_id}}/start"
JOB_LEASE_PATH = f"{JOBS_PATH}/{{execution_id}}/lease"
JOB_RESULTS_PATH = f"{JOBS_PATH}/{{execution_id}}/results"
JOB_FAIL_PATH = f"{JOBS_PATH}/{{execution_id}}/fail"
JOB_RELEASE_PATH = f"{JOBS_PATH}/{{execution_id}}/release"
ACCEPTED_STATUS = 202
INVALID_REQUEST = (400, "validation_error", "Invalid request")
LEASE_OWNER_MISSING = (400, "job.lease-owner-missing", "Lease owner header is required")
NOT_FOUND = (404, "not_found", "Job execution not found")
IDEMPOTENCY_CONFLICT = (
    409,
    "job.idempotency-conflict",
    "Idempotency key was already used with different job input",
)
LEASE_HELD = (409, "job.lease-held", "Job lease is held by another runner")


class JobEnqueueBody(BaseModel):
    """계약이 정한 잡 접수 본문이며 브라우저는 백엔드마다 본문을 가르지 않는다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["title.suggestion", "recipe.scan", "task.cleanup", "rule.generation"]
    input: JsonObject = Field(default_factory=dict)
    idempotencyKey: str | None = Field(default=None, min_length=1, max_length=200)


router = APIRouter()


def get_scan_anchors(request: Request) -> ScanAnchorSource:
    """스캔 앵커 창구를 애플리케이션 상태에서 꺼낸다."""
    anchors: ScanAnchorSource = request.app.state.scan_anchors
    return anchors


def get_rule_anchors(request: Request) -> RuleAnchorSource:
    """규칙 생성의 근거를 추적 창구에서 읽는 통로를 낸다."""
    anchors: RuleAnchorSource = request.app.state.rule_anchors
    return anchors


def get_model_credentials(request: Request) -> ModelCredentialSource:
    """접수가 이 사용자의 모델 자격을 보는 통로를 낸다."""
    credentials: ModelCredentialSource = request.app.state.model_credentials
    return credentials


def get_job_dispatch(request: Request) -> TemporalJobDispatch:
    """잡 실행을 워커에게 맡기는 통로를 낸다."""
    dispatch: TemporalJobDispatch = request.app.state.job_dispatch
    return dispatch


RuleAnchors = Annotated[RuleAnchorSource, Depends(get_rule_anchors)]
ScanAnchors = Annotated[ScanAnchorSource, Depends(get_scan_anchors)]
ModelCredentials = Annotated[ModelCredentialSource, Depends(get_model_credentials)]
JobDispatch = Annotated[TemporalJobDispatch, Depends(get_job_dispatch)]


@router.post(
    JOBS_PATH,
    status_code=ACCEPTED_STATUS,
    response_model=SuccessEnvelope,
    responses=error_responses(400, 404, 409),
)
async def enqueue_job(
    request: Request,
    source: ExecutionSql,
    user_id: UserId,
    anchors: RuleAnchors,
    scan_anchors: ScanAnchors,
    credentials: ModelCredentials,
    dispatch: JobDispatch,
) -> JSONResponse:
    """잡 하나를 접수하고 원장 행이나 사유를 계약이 정한 봉투로 낸다."""
    body = await read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        enqueue = JobEnqueueBody.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))

    try:
        job_input = INPUT_MODEL_BY_KIND[enqueue.kind].model_validate(enqueue.input)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))

    rejection = await job_input.admit(AdmissionContext(user_id, anchors, scan_anchors))
    if rejection is not None:
        return error_envelope(rejection.status, rejection.code, rejection.message)
    # 자격을 접수가 보지 않으면 대기 행이 선 뒤 워커에서 실패해 사용자가 사유를 늦게 받는다.
    if not runs_locally(enqueue.kind) and not await credentials.api_key(user_id):
        return error_envelope(*JOB_KEY_MISSING)

    idempotency_key = _idempotency_key(enqueue.idempotencyKey)
    request_hash = None if idempotency_key is None else input_hash(enqueue.kind, job_input)
    now = datetime.now(UTC)
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
        await dispatch.start(AgentJobKind.of_wire(enqueue.kind), job_id, payload)

    return JSONResponse(status_code=ACCEPTED_STATUS, content={"ok": True, "data": {"job": job_dto(row)}})


@router.post(JOB_CANCEL_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def cancel_job(
    execution_id: str, source: ExecutionSql, user_id: UserId, dispatch: JobDispatch
) -> JSONResponse:
    """진행 중인 잡 하나를 끊고 원장 행이나 사유를 계약이 정한 봉투로 낸다."""
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
        await dispatch.cancel(AgentJobKind.of_wire(kind), execution_id)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"job": job_dto(row)}})


def _idempotency_key(value: str | None) -> str | None:
    """공백뿐인 멱등키는 키를 싣지 않은 것과 같게 본다."""
    trimmed = (value or "").strip()
    return trimmed or None


class JobExecutionUsage(BaseModel):
    """실행기가 잰 관측이며 칸의 이름은 워크플로 축이 원장에 적는 것과 같다."""

    model_config = ConfigDict(extra="forbid")

    model: str | None
    durationMs: int | None
    costUsd: float | None
    numTurns: int | None
    inputTokens: int | None = None
    outputTokens: int | None = None
    cacheReadTokens: int | None = None
    cacheCreationTokens: int | None = None


class JobReportBody(BaseModel):
    """실행기가 만든 규칙과 그 근거이며 잡의 산출물 자리에 그대로 실린다."""

    model_config = ConfigDict(extra="forbid")

    rules: list[JsonObject]
    skipped: list[str] | None = None
    usage: JobExecutionUsage
    steps: list[AgentStepDTO]


class JobFailureBody(BaseModel):
    """실행기가 보고하는 실패이며 원장의 오류 자리에 남는다."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    retryable: bool = False
    usage: JobExecutionUsage
    steps: list[AgentStepDTO]


@router.post(JOB_START_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404, 409))
async def claim_job(
    execution_id: str, source: ExecutionSql, user_id: UserId, owner: LeaseOwner
) -> JSONResponse:
    """대기 중인 잡 하나를 부른 실행기의 리스로 가져간다."""
    if not owner:
        return error_envelope(*LEASE_OWNER_MISSING)
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


@router.post(JOB_LEASE_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404))
async def renew_job_lease(
    execution_id: str, source: ExecutionSql, user_id: UserId, owner: LeaseOwner
) -> JSONResponse:
    """실행이 길어지는 동안 쥔 리스의 수명을 늘린다."""
    if not owner:
        return error_envelope(*LEASE_OWNER_MISSING)
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


@router.post(JOB_RESULTS_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404, 409))
async def report_job_result(
    execution_id: str,
    request: Request,
    source: ExecutionSql,
    user_id: UserId,
    owner: LeaseOwner,
) -> JSONResponse:
    """실행기가 만든 산출물을 잡에 싣고 종결한다."""
    parsed = await _parse_body(request, JobReportBody)
    if isinstance(parsed, JSONResponse):
        return parsed
    rules = parsed.model_dump(exclude_none=True, exclude={"usage", "steps"})
    return await _settle(execution_id, source, user_id, owner, "completed", rules, None, parsed)


@router.post(JOB_FAIL_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404, 409))
async def fail_job(
    execution_id: str,
    request: Request,
    source: ExecutionSql,
    user_id: UserId,
    owner: LeaseOwner,
) -> JSONResponse:
    """실행기가 실패를 보고하고 잡을 종결한다."""
    parsed = await _parse_body(request, JobFailureBody)
    if isinstance(parsed, JSONResponse):
        return parsed
    return await _settle(execution_id, source, user_id, owner, "failed", {}, parsed.message, parsed)


@router.post(JOB_RELEASE_PATH, response_model=SuccessEnvelope, responses=error_responses(400, 404))
async def release_job(
    execution_id: str, source: ExecutionSql, user_id: UserId, owner: LeaseOwner
) -> JSONResponse:
    """끝내지 못한 실행기가 리스를 놓아 잡을 곧바로 대기로 되돌린다."""
    if not owner:
        return error_envelope(*LEASE_OWNER_MISSING)
    now = datetime.now(UTC)
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        released = await ledger.release_lease(execution_id, owner, now)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"released": released}})


async def _parse_body[BodyT: BaseModel](request: Request, model: type[BodyT]) -> BodyT | JSONResponse:
    """본문을 계약이 정한 봉투로 거절하며 FastAPI 의 자동 검증에 맡기지 않는다."""
    body = await read_body(request)
    if body is None:
        return error_envelope(*INVALID_REQUEST)
    try:
        return model.model_validate(body)
    except ValidationError as invalid:
        return error_envelope(*INVALID_REQUEST, details=validation_details(invalid))


async def _settle(
    execution_id: str,
    source: SqlSource,
    user_id: str,
    owner: str,
    status: str,
    result: JsonObject,
    error: str | None,
    reported: JobReportBody | JobFailureBody,
) -> JSONResponse:
    if not owner:
        return error_envelope(*LEASE_OWNER_MISSING)
    now = datetime.now(UTC)
    usage = reported.usage.model_dump(exclude_none=True)
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        if not await ledger.settle_with_lease(execution_id, owner, status, result, error, usage, now):
            return error_envelope(*LEASE_HELD)
        # 회차는 원장이 세고 계약이 1부터 세므로 리스만 쥔 잡의 첫 시도를 1로 적는다.
        attempt = max(int(row["attempts"]), 1)
        await ledger.record_steps(execution_id, user_id, attempt, reported.steps, now)
        row = await ledger.find(execution_id) or row
    return JSONResponse(status_code=200, content={"ok": True, "data": {"job": job_dto(row)}})


def _lease(owner: str, expires_at: datetime) -> JsonObject:
    return {"held": True, "leaseOwner": owner, "leaseExpiresAt": opt_iso(expires_at)}


def _lease_of(row: Mapping[str, Any], owner: str, now: datetime) -> JsonObject:
    """쥔 사람이 부른 사람과 같고 아직 살아 있을 때만 쥔 것으로 본다."""
    held_by = row["lease_owner"]
    expires_at = row["lease_expires_at"]
    alive = expires_at is not None and expires_at > now
    return {
        "held": bool(alive and held_by == owner),
        "leaseOwner": held_by,
        "leaseExpiresAt": None if expires_at is None else opt_iso(expires_at),
    }
