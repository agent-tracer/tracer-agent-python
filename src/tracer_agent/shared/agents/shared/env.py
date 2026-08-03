"""환경변수 하나를 켬과 끔으로 읽는 방식을 한 곳에 둔다."""

from __future__ import annotations

import os

_TRUE = "true"
_FALSE = "false"


def env_flag(name: str, *, default: bool = False) -> bool:
    """배포가 명시로 켜거나 끈 값만 따르고 그 밖에는 기본값을 쓴다."""
    declared = os.environ.get(name, "").strip().lower()
    if declared == _TRUE:
        return True
    if declared == _FALSE:
        return False
    return default
