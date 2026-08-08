"""브라우저와 배포 단위가 함께 읽는 HTTP 응답 봉투의 모양과 그 봉투를 적는 방법을 소유한다."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from .json_view import JsonObject, JsonValue

OK_STATUS = 200

# 스키마를 만족하지 않는 요청의 거절이며 접수와 조회의 모든 창구가 같은 글자를 낸다.
INVALID_REQUEST = (400, "validation_error", "Invalid request")
# 남의 실행을 존재조차 알리지 않는 거절이며 대화 표면과 봉투 창구가 같은 글자를 낸다.
EXECUTION_NOT_FOUND = (404, "not_found", "Chat execution not found")


class MalformedEnvelope(ValueError):
    """상류가 계약이 정한 성공 봉투로 답하지 않아 실은 것을 꺼낼 수 없다."""


def unwrap_envelope(payload: JsonValue) -> JsonValue:
    """계약이 정한 성공 봉투에서 실은 것을 꺼낸다."""
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise MalformedEnvelope("payload is not a success envelope")
    return payload.get("data")


class WireError(BaseModel):
    """실패 사유이며 판정 근거가 있을 때만 details 를 싣는다."""

    code: str
    message: str
    details: Any = Field(default=None)


class ErrorEnvelope(BaseModel):
    """계약이 정한 실패 봉투다."""

    ok: Literal[False] = False
    error: WireError


class SuccessEnvelope(BaseModel):
    """계약이 정한 성공 봉투이며 실은 것의 모양은 창구마다 다르다."""

    ok: Literal[True] = True
    data: Any


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """이 창구가 낼 수 있는 실패 상태마다 계약의 오류 봉투를 선언한다."""
    return {status: {"model": ErrorEnvelope} for status in statuses}


def ok(data: Any, status: int = OK_STATUS) -> JSONResponse:
    """실은 것을 계약이 정한 성공 봉투에 담는다."""
    return JSONResponse(status_code=status, content={"ok": True, "data": data})


def error_envelope(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    """실패 사유를 계약이 정한 오류 봉투로 적는다."""
    error: JsonObject = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"ok": False, "error": error})


async def read_body(request: Request) -> JsonObject | None:
    """요청 본문을 객체 하나로 읽으며 JSON 이 아니거나 객체가 아니면 아무것도 내지 않는다."""
    try:
        body = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def validation_details(invalid: ValidationError) -> JsonValue:
    """스키마 위반의 근거를 봉투에 실을 수 있는 값으로 적는다."""
    details: JsonValue = json.loads(
        invalid.json(include_url=False, include_context=False, include_input=False)
    )
    return details
