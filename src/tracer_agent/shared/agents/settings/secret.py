"""자격 설정이 저장되는 암호 형식과 그 키 유도를 소유한다."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENCRYPTED_PREFIX = "enc:v1:"
IV_BYTES = 12
GCM_TAG_BYTES = 16
PRD_PROFILE = "prd"

# 소스 실행과 컨테이너 실행이 같은 로컬 데이터베이스를 공유하므로 두 경로의 기본 키가 같아야 한다.
INSECURE_LOCAL_DEV_KEY = "monitor-dev-key"


class SecretKeyMissing(Exception):
    """prd 프로파일은 자격을 풀 키를 스스로 정하지 않는다."""


class SecretKeyMismatch(Exception):
    """저장할 때 쓴 키와 지금 키가 달라 복호화가 성립하지 않는다."""


class MalformedSecret(Exception):
    """저장된 값이 자격 암호 형식이 아니다."""


def is_encrypted_secret(value: str) -> bool:
    """저장된 값이 자격 암호 형식인지 답한다."""
    return value.startswith(ENCRYPTED_PREFIX)


class SettingCipher:
    """자격 설정을 초기벡터와 인증태그와 암호문 한 문자열로 감추고 되돌린다."""

    def __init__(self, secret: str | None, profile: str) -> None:
        self._secret = secret
        self._profile = profile
        self._key: bytes | None = None

    def encrypt(self, plaintext: str) -> str:
        """평문을 지금 키로 감추고 초기벡터와 인증태그와 암호문을 한 문자열에 담는다."""
        iv = os.urandom(IV_BYTES)
        sealed = AESGCM(self._resolve_key()).encrypt(iv, plaintext.encode(), None)
        ciphertext, tag = sealed[:-GCM_TAG_BYTES], sealed[-GCM_TAG_BYTES:]
        parts = (_encode(iv), _encode(tag), _encode(ciphertext))
        return f"{ENCRYPTED_PREFIX}{':'.join(parts)}"

    def decrypt(self, stored: str) -> str:
        """감춰진 값을 지금 키로 되돌리며 키가 다르면 그 사실을 알린다."""
        if not is_encrypted_secret(stored):
            raise MalformedSecret("Value is not in the expected encrypted format.")
        parts = stored[len(ENCRYPTED_PREFIX) :].split(":")
        if len(parts) != 3 or not all(parts):
            raise MalformedSecret("Malformed encrypted value.")
        iv, tag, ciphertext = (base64.b64decode(part) for part in parts)
        try:
            return AESGCM(self._resolve_key()).decrypt(iv, ciphertext + tag, None).decode()
        except InvalidTag as mismatch:
            raise SecretKeyMismatch(
                "저장된 설정을 지금 키로 풀 수 없으니 키를 되돌리거나 그 설정을 다시 넣어야 한다"
            ) from mismatch

    def _resolve_key(self) -> bytes:
        key = self._key
        if key is None:
            key = _derive_key(self._secret, self._profile)
            self._key = key
        return key


def _derive_key(secret: str | None, profile: str) -> bytes:
    if not secret:
        if profile == PRD_PROFILE:
            raise SecretKeyMissing("MONITOR_SETTINGS_ENCRYPTION_KEY must be set in the prd profile.")
        return hashlib.sha256(INSECURE_LOCAL_DEV_KEY.encode()).digest()
    return hashlib.sha256(secret.encode()).digest()


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode()
