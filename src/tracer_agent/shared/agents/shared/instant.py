"""두 구현체가 같은 글자로 적고 같은 값으로 읽는 시각 표현을 소유한다."""

from __future__ import annotations

from datetime import UTC, datetime

# 자바스크립트 Date 가 내는 표현은 UTC 를 이 글자로 적는다.
_UTC_SUFFIX = "Z"
_UTC_OFFSET = "+00:00"


def iso(value: datetime) -> str:
    """시각을 자바스크립트 Date가 내는 밀리초 세 자리 UTC 문자열로 적는다."""
    moment = value.astimezone(UTC)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond // 1000:03d}{_UTC_SUFFIX}"


def opt_iso(value: datetime | None) -> str | None:
    """비어 있을 수 있는 시각 자리를 같은 규칙으로 적는다."""
    return None if value is None else iso(value)


def parse_instant(value: str) -> datetime:
    """자바스크립트 Date가 적은 UTC 표현을 시각으로 되읽는다."""
    return datetime.fromisoformat(value.replace(_UTC_SUFFIX, _UTC_OFFSET))
