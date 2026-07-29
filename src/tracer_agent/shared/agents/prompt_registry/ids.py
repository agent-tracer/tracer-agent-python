"""등록이 세우는 행의 식별자를 원장의 다른 표가 쓰는 것과 같은 ULID 규칙으로 만든다."""

from __future__ import annotations

import secrets
from datetime import datetime

ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
TIME_LENGTH = 10
RANDOM_LENGTH = 16


def generate_ulid(now: datetime) -> str:
    """밀리초 시각 10자와 난수 16자를 이어 붙인 26자 식별자를 낸다."""
    return _encode_time(int(now.timestamp() * 1000)) + _encode_random()


def _encode_time(time_ms: int) -> str:
    value = max(time_ms, 0)
    digits = []
    for _ in range(TIME_LENGTH):
        digits.append(ENCODING[value % 32])
        value //= 32
    return "".join(reversed(digits))


def _encode_random() -> str:
    return "".join(ENCODING[secrets.randbelow(32)] for _ in range(RANDOM_LENGTH))
