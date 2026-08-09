"""설정 원장의 값 가운데 잡의 실행 입력으로 실리는 것을 계약이 적은 대로 고른다."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from ..shared.contract_root import CONTRACT_ROOT

SETTINGS_EXECUTION_PATH = CONTRACT_ROOT / "agent" / "shared" / "settings.execution.json"
LANGUAGES_PATH = CONTRACT_ROOT / "agent" / "shared" / "languages.json"

LANGUAGE_FIELD = "language"
MAX_SUGGESTIONS_FIELD = "maxSuggestions"


@lru_cache(maxsize=1)
def _settings_execution() -> Mapping[str, Any]:
    """설정과 실행 입력을 잇는 계약 조각 전체다."""
    declared: Mapping[str, Any] = json.loads(SETTINGS_EXECUTION_PATH.read_text(encoding="utf-8"))
    return declared


def _inputs() -> Mapping[str, Any]:
    """설정이 실행 입력에 실리는 자리를 계약에서 읽는다."""
    inputs: Mapping[str, Any] = _settings_execution()["inputs"]
    return inputs


@lru_cache(maxsize=1)
def _languages() -> frozenset[str]:
    """계약이 허용한 출력 언어의 목록이다."""
    declared: Mapping[str, Any] = json.loads(LANGUAGES_PATH.read_text(encoding="utf-8"))
    return frozenset(declared["languages"])


def setting_key(field: str) -> str:
    """그 실행 입력 칸을 채우는 설정의 열쇠다."""
    return str(_inputs()[field]["setting"])


def carries(field: str, kind: str) -> bool:
    """그 잡 종류가 이 칸을 설정에서 채우는지다."""
    return kind in _inputs()[field]["kinds"]


def default_language() -> str:
    """설정도 요청도 없을 때 쓰는 출력 언어다."""
    return str(_inputs()[LANGUAGE_FIELD]["default"])


@lru_cache(maxsize=1)
def fallback_language() -> str:
    """계약의 onUnknown 대로 목록 밖의 언어를 받았을 때 대신 쓰는 언어다."""
    declared: Mapping[str, Any] = json.loads(LANGUAGES_PATH.read_text(encoding="utf-8"))
    return str(declared["default"])


def default_max_suggestions() -> int:
    """설정도 요청도 없을 때 쓰는 정리 제안 개수다."""
    return int(_inputs()[MAX_SUGGESTIONS_FIELD]["default"])


def normalize_language(raw: str | None) -> str:
    """앞뒤 공백을 없애고 소문자로 만들며 계약의 목록에 없는 값은 기본값으로 본다."""
    if raw is None:
        return default_language()
    normalized = raw.strip().lower()
    return normalized if normalized in _languages() else default_language()


def normalize_max_suggestions(raw: str | None, cap: int) -> int:
    """10진 정수로 읽어 1 과 상한 사이로 조이며 정수로 읽히지 않으면 기본값으로 본다."""
    try:
        parsed = int(str(raw).strip(), 10)
    except (TypeError, ValueError):
        return default_max_suggestions()
    return min(max(parsed, 1), cap)


@lru_cache(maxsize=1)
def model_kinds() -> tuple[str, ...]:
    """설정이 고른 모델이 실리는 잡 종류다."""
    return tuple(str(kind) for kind in _settings_execution()["envelope"]["model"]["kinds"])
