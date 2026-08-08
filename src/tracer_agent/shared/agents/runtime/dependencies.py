"""요청이 실행 원장과 자기신고 사용자 신원에 닿는 자리를 창구의 시그니처로 드러낸다."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from .ledger import SqlSource

MONITOR_USER_HEADER = "x-monitor-user"
MONITOR_LEASE_OWNER_HEADER = "x-monitor-lease-owner"
DEFAULT_USER_ID = "local"


def get_execution_sql(request: Request) -> SqlSource:
    """앱 수명이 연 실행 원장 연결원을 낸다."""
    source: SqlSource = request.app.state.services.execution_sql
    return source


def resolve_user_id(header: str | None) -> str:
    """자기신고 사용자 헤더가 비면 계약이 정한 기본 사용자로 읽는다."""
    trimmed = (header or "").strip()
    return trimmed if trimmed else DEFAULT_USER_ID


def get_user_id(monitor_user: Annotated[str | None, Header(alias=MONITOR_USER_HEADER)] = None) -> str:
    """이 요청이 자기신고한 사용자를 낸다."""
    return resolve_user_id(monitor_user)


def get_lease_owner(
    monitor_lease_owner: Annotated[str | None, Header(alias=MONITOR_LEASE_OWNER_HEADER)] = None,
) -> str:
    """리스를 쥔 실행기의 이름이며 없으면 빈 값이라 부르는 창구가 거절을 낸다."""
    return (monitor_lease_owner or "").strip()


ExecutionSql = Annotated[SqlSource, Depends(get_execution_sql)]
UserId = Annotated[str, Depends(get_user_id)]
LeaseOwner = Annotated[str, Depends(get_lease_owner)]
