"""잡 원장의 조회를 계약이 정한 경로와 봉투로 낸다."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..agents.runtime.dependencies import ExecutionSql, UserId
from ..agents.shared.wire import SuccessEnvelope, error_responses
from .jobs_intake import INVALID_REQUEST, JOBS_PATH, NOT_FOUND, error_envelope
from .jobs_ledger import JobLedger
from .jobs_view import job_dto, job_step_dto

# 고정 경로를 잡 식별자 경로보다 먼저 등록해야 history와 latest가 식별자로 잡히지 않는다.
JOB_HISTORY_PATH = f"{JOBS_PATH}/history"
JOB_LATEST_PATH = f"{JOBS_PATH}/latest"
JOB_PATH = f"{JOBS_PATH}/{{execution_id}}"
JOB_STEPS_PATH = f"{JOB_PATH}/steps"

# 원장에 남을 수 있는 잡의 종류이며 접수가 받는 것보다 넓다.
JOB_LEDGER_KINDS: tuple[str, ...] = ("title.suggestion", "recipe.scan", "task.cleanup", "rule.generation")
JOB_STATUSES: tuple[str, ...] = ("pending", "running", "completed", "failed", "canceled")
PENDING = "pending"

HISTORY_LIMIT_DEFAULT = 50
HISTORY_LIMIT_MAX = 100

router = APIRouter()


@router.get(JOBS_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def list_pending_jobs(request: Request, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """종류별 대기 잡 중 이 사용자의 것을 접수 시각의 오름차순으로 낸다."""
    kind = request.query_params.get("kind")
    status = request.query_params.get("status")
    if kind not in JOB_LEDGER_KINDS or (status is not None and status != PENDING):
        return error_envelope(*INVALID_REQUEST)
    async with source.connect() as sql:
        rows = await JobLedger(sql).pending(kind)
    return _ok({"items": [job_dto(row) for row in rows if row["user_id"] == user_id]})


@router.get(JOB_HISTORY_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def list_job_history(request: Request, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """이 사용자의 잡 이력을 접수 시각의 내림차순으로 한 페이지씩 낸다."""
    kind = request.query_params.get("kind")
    status = request.query_params.get("status")
    if (kind is not None and kind not in JOB_LEDGER_KINDS) or (
        status is not None and status not in JOB_STATUSES
    ):
        return error_envelope(*INVALID_REQUEST)
    page = _page(request.query_params.get("limit"), request.query_params.get("offset"))
    if page is None:
        return error_envelope(*INVALID_REQUEST)
    async with source.connect() as sql:
        rows, total = await JobLedger(sql).history(user_id, kind, status, *page)
    return _ok({"items": [job_dto(row) for row in rows], "total": total})


@router.get(JOB_LATEST_PATH, response_model=SuccessEnvelope, responses=error_responses(400))
async def get_latest_job(request: Request, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """사용자와 종류와 태스크 조합의 가장 최근 잡을 내며 없으면 빈 자리를 낸다."""
    kind = request.query_params.get("kind")
    if kind not in JOB_LEDGER_KINDS:
        return error_envelope(*INVALID_REQUEST)
    task_id = request.query_params.get("taskId")
    async with source.connect() as sql:
        row = await JobLedger(sql).latest(user_id, kind, task_id or None)
    return _ok({"job": None if row is None else job_dto(row)})


@router.get(JOB_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def get_job(execution_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """잡 하나의 원장 행을 낸다."""
    async with source.connect() as sql:
        row = await JobLedger(sql).find(execution_id)
    if row is None or row["user_id"] != user_id:
        return error_envelope(*NOT_FOUND)
    return _ok({"job": job_dto(row)})


@router.get(JOB_STEPS_PATH, response_model=SuccessEnvelope, responses=error_responses(404))
async def get_job_steps(execution_id: str, source: ExecutionSql, user_id: UserId) -> JSONResponse:
    """잡 하나가 남긴 궤적을 시도와 순번의 오름차순으로 낸다."""
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        steps = await ledger.steps(execution_id, user_id)
    return _ok([job_step_dto(one) for one in steps])


def _page(limit: str | None, offset: str | None) -> tuple[int, int] | None:
    """상한과 건너뛰기를 읽고 범위를 벗어나면 조회하지 않는다."""
    try:
        parsed_limit = HISTORY_LIMIT_DEFAULT if limit is None else int(limit)
        parsed_offset = 0 if offset is None else int(offset)
    except ValueError:
        return None
    if not 1 <= parsed_limit <= HISTORY_LIMIT_MAX or parsed_offset < 0:
        return None
    return parsed_limit, parsed_offset


def _ok(data: object) -> JSONResponse:
    return JSONResponse(status_code=200, content={"ok": True, "data": data})
