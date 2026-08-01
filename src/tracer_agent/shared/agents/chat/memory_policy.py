"""장기 기억에 실을 수 없는 내용을 판정한다."""

from __future__ import annotations

import re

# 저장된 문장은 다음 턴의 문맥으로 되돌아오므로 지시문과 자격 증명은 실리기 전에 막는다.
_INSTRUCTION_PATTERNS = (
    re.compile(r"\bignore\s+(all\s+|any\s+)?(previous|prior|above)\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(all\s+|any\s+)?(previous|prior|above)\b", re.IGNORECASE),
    re.compile(r"^\s*(system|assistant|developer)\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\byou\s+(must|should|will)\s+(always|never)\b", re.IGNORECASE),
    re.compile(r"\balways\s+call\b", re.IGNORECASE),
    re.compile(r"<\s*/?\s*(system|memory|summary|history|tool_result)\b", re.IGNORECASE),
)

_SECRET_PATTERNS = (
    re.compile(r"\b(sk|pk|ghp|gho|xox[baprs])-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[A-Z0-9_]*(?:API|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*\s*=\s*\S+"),
    re.compile(r"\bauthorization\s*:\s*\S+", re.IGNORECASE),
)

INSTRUCTION_REJECTION = "memory-content-looks-like-an-instruction"
SECRET_REJECTION = "memory-content-contains-a-secret"


def memory_rejection(content: str) -> str | None:
    """실을 수 없는 내용이면 그 사유를 내고 실을 수 있으면 None 을 낸다."""
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        return SECRET_REJECTION
    if any(pattern.search(content) for pattern in _INSTRUCTION_PATTERNS):
        return INSTRUCTION_REJECTION
    return None
