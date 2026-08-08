"""브라우저의 잡 접수와 취소 요청을 계약이 정한 경로와 봉투와 오류 형식으로 받는다."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..agents.envelope.models import ModelCredentialSource
from ..agents.runtime.dependencies import ExecutionSql, UserId
from ..agents.shared.models import AgentStepDTO
from ..agents.shared.wire import (
    INVALID_REQUEST,
    SuccessEnvelope,
    error_envelope,
    error_responses,
    read_body,
    validation_details,
)
from .jobs_anchor import ScanAnchorSource
from .jobs_dispatch import TemporalJobDispatch
from .jobs_enqueue import NOT_FOUND, JobEnqueueBody, JobIntake, JobRejected
from .jobs_kinds import AgentJobKind
from .jobs_ledger import JobLedger
from .jobs_view import job_dto

JOBS_PATH = "/api/agent/jobs"
JOB_CANCEL_PATH = f"{JOBS_PATH}/{{execution_id}}/cancel"
ACCEPTED_STATUS = 202


router = APIRouter()


def get_scan_anchors(request: Request) -> ScanAnchorSource:
    """스캔 앵커 창구를 애플리케이션 상태에서 꺼낸다."""
    anchors: ScanAnchorSource = request.app.state.services.scan_anchors
    return anchors


def get_model_credentials(request: Request) -> ModelCredentialSource:
    """접수가 이 사용자의 모델 자격을 보는 통로를 낸다."""
    credentials: ModelCredentialSource = request.app.state.services.model_credentials
    return credentials


def get_job_dispatch(request: Request) -> TemporalJobDispatch:
    """잡 실행을 워커에게 맡기는 통로를 낸다."""
    dispatch: TemporalJobDispatch = request.app.state.services.job_dispatch
    return dispatch


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

    intake = JobIntake(scan_anchors, credentials, dispatch)
    try:
        # 연결을 쥔 채 자격을 조회하면 한 요청이 연결 둘을 요구해 풀이 자기 자신을 기다린다.
        admitted = await intake.admit(user_id, enqueue)
        async with source.connect() as sql:
            accepted = await intake.claim(sql, user_id, enqueue, admitted, datetime.now(UTC))
        await intake.start(enqueue.kind, accepted)
    except JobRejected as rejected:
        return error_envelope(rejected.status, rejected.code, rejected.message, rejected.details)

    return JSONResponse(
        status_code=ACCEPTED_STATUS, content={"ok": True, "data": {"job": job_dto(accepted.row)}}
    )


@router.post(JOB_CANCEL_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def cancel_job(
    execution_id: str, source: ExecutionSql, user_id: UserId, dispatch: JobDispatch
) -> JSONResponse:
    """진행 중인 잡 하나를 끊고 원장 행이나 사유를 계약이 정한 봉투로 낸다."""
    async with source.connect() as sql:
        row = await JobLedger(sql).find(execution_id)
    if row is None or row["user_id"] != user_id:
        return error_envelope(*NOT_FOUND)

    # 전이를 먼저 하면 취소에 실패했을 때 취소됐다고 기록한 채 유료 실행이 이어진다.
    await dispatch.cancel(AgentJobKind.of_wire(str(row["kind"])), execution_id)
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        if await ledger.cancel(execution_id, datetime.now(UTC)):
            row = await ledger.find(execution_id) or row
    return JSONResponse(status_code=200, content={"ok": True, "data": {"job": job_dto(row)}})


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
