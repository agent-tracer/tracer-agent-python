"""대화 창구가 거절을 계약이 정한 봉투로 적고 본문을 자기 모델로 해석한다."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ...shared.wire import INVALID_REQUEST, error_envelope, read_body, validation_details
from ..rejections import ChatRejected
from ..tools.surface import chat_argument_rejection

CREATED_STATUS = 201

__all__ = [
    "CREATED_STATUS",
    "argument_rejection",
    "invalid_request",
    "read_payload",
    "rejection",
]


def rejection(rejected: ChatRejected) -> JSONResponse:
    """거절 사유를 계약이 정한 오류 봉투로 적는다."""
    return error_envelope(rejected.status, rejected.code, rejected.message)


def argument_rejection(action: str, missing: tuple[str, ...]) -> JSONResponse:
    """action 이 요구하는 인자가 빠진 호출을 계약이 정한 코드와 빠진 자리로 돌려보낸다."""
    declared = chat_argument_rejection()
    return error_envelope(
        int(declared["status"]),
        str(declared["code"]),
        str(declared["message"]),
        details={"action": action, "missing": list(missing)},
    )


def invalid_request(details: Any = None) -> JSONResponse:
    """스키마를 만족하지 않는 본문을 계약이 정한 코드로 돌려보낸다."""
    return error_envelope(*INVALID_REQUEST, details=details)


async def read_payload[Payload: BaseModel](request: Request, model: type[Payload]) -> Payload | JSONResponse:
    """요청 본문을 모델로 해석하고 어긋나면 거절 봉투를 낸다."""
    body = await read_body(request)
    if body is None:
        return invalid_request()
    try:
        return model.model_validate(body)
    except ValidationError as invalid:
        return invalid_request(validation_details(invalid))
