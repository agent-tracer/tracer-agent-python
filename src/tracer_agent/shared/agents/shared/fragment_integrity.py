"""프롬프트 조각 본문의 정규화와 내용 해시와 placeholder 규칙을 소유한다."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def canonical_fragment_content(content: str) -> str:
    """줄바꿈만 LF로 맞추고 NFC 정규화하며 공백과 끝 개행은 보존한다."""
    return unicodedata.normalize("NFC", content.replace("\r\n", "\n").replace("\r", "\n"))


def fragment_content_hash(content: str) -> str:
    """정규화한 조각 본문의 sha256을 낸다."""
    canonical = canonical_fragment_content(content)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fragment_placeholders(content: str) -> tuple[str, ...]:
    """조각 본문이 채울 자리의 이름을 중복 없이 정렬해 낸다."""
    return tuple(sorted(set(_PLACEHOLDER.findall(content))))
