"""해소된 확인 하나가 낳는 턴의 접수 식별자와 입력 해시를 빚는다."""

from __future__ import annotations

import hashlib

_FOLLOW_UP_REQUEST_PREFIX = "confirmation:"


def follow_up_client_request_id(confirmation_id: str) -> str:
    """확인 하나가 낳는 턴의 접수 식별자이며 같은 확인이면 언제 불러도 같은 값이다."""
    return f"{_FOLLOW_UP_REQUEST_PREFIX}{confirmation_id}"


def follow_up_input_hash(confirmation_id: str) -> str:
    """이 턴의 입력은 해소된 확인 하나이므로 그 식별자가 곧 입력의 지문이다."""
    return hashlib.sha256(confirmation_id.encode()).hexdigest()
