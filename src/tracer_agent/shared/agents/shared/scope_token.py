"""실행 하나에 매인 자격을 계약이 정한 모양으로 발급한다."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from functools import lru_cache
from typing import Any

from .contract_root import CONTRACT_ROOT

SCOPE_TOKEN_PATH = CONTRACT_ROOT / "agent" / "shared" / "scope.token.json"


@lru_cache(maxsize=1)
def _rule() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(SCOPE_TOKEN_PATH.read_text(encoding="utf-8"))
    return document


@lru_cache(maxsize=1)
def prefix() -> str:
    """인증 베어러와 섞이지 않게 이 자격만 갖는 접두사다."""
    return str(_rule()["prefix"])


@lru_cache(maxsize=1)
def margin_ms() -> int:
    """마감 직전에 시작한 도구 호출이 자격을 잃지 않도록 마감보다 더 사는 시간이다."""
    return int(_rule()["lifetime"]["marginMs"])


def secret_variable() -> str:
    """계약이 정한 서명 비밀의 환경변수 이름이다."""
    return str(_rule()["secret"]["variable"])


def read_secret() -> str | None:
    """런타임 경계에서 서명 비밀을 한 번 읽으며 비어 있으면 아무것도 내지 않는다."""
    raw = os.environ.get(secret_variable())
    if raw is None:
        return None
    trimmed = raw.strip() if bool(_rule()["secret"]["trim"]) else raw
    return trimmed if trimmed else None


def _secret() -> str | None:
    return read_secret()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url(digest)


def issue(*, user_id: str, execution_id: str, ttl_ms: int, now_ms: int) -> str | None:
    """서명 비밀이 있을 때만 이 사용자와 실행에 매인 자격을 낸다."""
    secret = _secret()
    if secret is None:
        return None
    payload = _b64url(
        json.dumps(
            {
                "userId": user_id,
                "executionId": execution_id,
                "issuedAt": now_ms,
                "expiresAt": now_ms + ttl_ms,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{prefix()}.{payload}.{_sign(payload, secret)}"


def looks_like(candidate: str) -> bool:
    """모양이 맞으면 검증에 실패해도 자기신고 헤더로 되돌리지 않는다."""
    return candidate.startswith(f"{prefix()}.")


def verify(token: str, *, now_ms: int) -> tuple[str, str] | None:
    """서명과 수명이 모두 맞을 때만 사용자와 실행을 내주며 그 값이 자기신고 헤더를 우선한다."""
    secret = _secret()
    if secret is None:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    head, payload, signature = parts
    if head != prefix() or not payload or not signature:
        return None
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return None
    padded = payload + "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except ValueError:
        return None
    if not isinstance(claims, dict):
        return None
    expires_at = claims.get("expiresAt")
    if not isinstance(expires_at, int) or expires_at <= now_ms:
        return None
    user_id, execution_id = claims.get("userId"), claims.get("executionId")
    if not isinstance(user_id, str) or not isinstance(execution_id, str):
        return None
    return user_id, execution_id
