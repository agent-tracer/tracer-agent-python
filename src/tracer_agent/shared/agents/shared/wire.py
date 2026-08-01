"""브라우저와 배포 단위가 함께 읽는 HTTP 응답 봉투의 모양을 계약대로 선언한다."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
