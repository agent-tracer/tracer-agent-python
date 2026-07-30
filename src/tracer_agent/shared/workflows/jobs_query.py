"""잡 하나와 그 실행 궤적의 조회를 계약이 정한 경로와 봉투로 낸다."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..agents.runtime.ledger import SqlSource
from .jobs_intake import JOBS_PATH, MONITOR_USER_HEADER, NOT_FOUND, error_envelope, resolve_user_id
from .jobs_ledger import JobLedger
from .jobs_view import job_dto, job_step_dto

JOB_PATH = f"{JOBS_PATH}/{{execution_id}}"
JOB_STEPS_PATH = f"{JOB_PATH}/steps"


async def get_job(execution_id: str, request: Request) -> JSONResponse:
    """잡 하나의 원장 행을 낸다."""
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        row = await JobLedger(sql).find(execution_id)
    if row is None or row["user_id"] != user_id:
        return error_envelope(*NOT_FOUND)
    return JSONResponse(status_code=200, content={"ok": True, "data": {"job": job_dto(row)}})


async def get_job_steps(execution_id: str, request: Request) -> JSONResponse:
    """잡 하나가 남긴 궤적을 시도와 순번의 오름차순으로 낸다."""
    user_id = resolve_user_id(request.headers.get(MONITOR_USER_HEADER))
    source: SqlSource = request.app.state.execution_sql
    async with source.connect() as sql:
        ledger = JobLedger(sql)
        row = await ledger.find(execution_id)
        if row is None or row["user_id"] != user_id:
            return error_envelope(*NOT_FOUND)
        steps = await ledger.steps(execution_id, user_id)
    return JSONResponse(status_code=200, content={"ok": True, "data": [job_step_dto(one) for one in steps]})
