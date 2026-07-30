"""대화 창구가 내는 성공과 실패 봉투와 본문 해석을 한자리에서 소유한다."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..intake.router import INVALID_REQUEST, error_envelope
from ..intake.turn import ChatIntakeRejected

OK_STATUS = 200
CREATED_STATUS = 201


def ok(data: Any, status: int = OK_STATUS) -> JSONResponse:
    """실은 것을 계약이 정한 성공 봉투에 담는다."""
    return JSONResponse(status_code=status, content={"ok": True, "data": data})


def rejection(rejected: ChatIntakeRejected) -> JSONResponse:
    """거절 사유를 계약이 정한 오류 봉투로 적는다."""
    return error_envelope(rejected.status, rejected.code, rejected.message)


def invalid_request(details: Any = None) -> JSONResponse:
    """스키마를 만족하지 않는 본문을 계약이 정한 코드로 돌려보낸다."""
    return error_envelope(*INVALID_REQUEST, details=details)


async def read_payload[Payload: BaseModel](request: Request, model: type[Payload]) -> Payload | JSONResponse:
    """요청 본문을 모델로 해석하고 어긋나면 거절 봉투를 낸다."""
    try:
        body = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return invalid_request()
    if not isinstance(body, dict):
        return invalid_request()
    try:
        return model.model_validate(body)
    except ValidationError as invalid:
        return invalid_request(
            json.loads(invalid.json(include_url=False, include_context=False, include_input=False))
        )
