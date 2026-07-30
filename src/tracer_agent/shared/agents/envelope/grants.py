"""실행 시도 하나에만 유효한 draft 창구 자격을 발급한다."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

DRAFT_TOKEN_BYTES = 32


@dataclass(frozen=True)
class DraftGrant:
    """평문은 실행기가 쓰고 지문은 원장이 드는 draft 자격 한 쌍이다."""

    token: str
    token_hash: str


def issue_draft_grant() -> DraftGrant:
    """draft 자격을 난수로 발급하고 되돌릴 수 없는 지문을 함께 낸다."""
    token = secrets.token_urlsafe(DRAFT_TOKEN_BYTES)
    return DraftGrant(token=token, token_hash=hashlib.sha256(token.encode()).hexdigest())
