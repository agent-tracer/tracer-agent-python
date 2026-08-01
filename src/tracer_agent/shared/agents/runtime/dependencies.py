"""요청이 실행 원장과 자기신고 사용자 신원에 닿는 자리를 창구의 시그니처로 드러낸다."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from .ledger import SqlSource

MONITOR_USER_HEADER = "x-monitor-user"
DEFAULT_USER_ID = "local"


def get_execution_sql(request: Request) -> SqlSource:
    """앱 수명이 연 실행 원장 연결원을 낸다."""
    source: SqlSource = request.app.state.execution_sql
    return source


def get_user_id(monitor_user: Annotated[str | None, Header(alias=MONITOR_USER_HEADER)] = None) -> str:
    """자기신고 사용자 헤더가 비면 계약이 정한 기본 사용자로 읽는다."""
    trimmed = (monitor_user or "").strip()
    return trimmed if trimmed else DEFAULT_USER_ID


ExecutionSql = Annotated[SqlSource, Depends(get_execution_sql)]
UserId = Annotated[str, Depends(get_user_id)]
