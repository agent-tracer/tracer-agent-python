"""설정 창구가 받는 본문과 내는 표현의 wire 계약과 가림 규칙을 소유한다."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from ..shared.instant import iso

SettingKey = Literal[
    "anthropic.api_key",
    "anthropic.model",
    "ruleGen.maxRulesPerTask",
    "taskCleanup.maxSuggestions",
    "claude.outputLanguage",
]

SETTING_KEYS: tuple[SettingKey, ...] = get_args(SettingKey)

MODEL_SETTING_KEY = "anthropic.model"
UNPRICED_MODEL_TYPE = "unpriced_model"

_SENSITIVE_KEYS: frozenset[str] = frozenset({"anthropic.api_key"})
_VISIBLE_TAIL = 4
_MASK_CHARACTER = "•"
_MASK_WIDTH = 8


class PutSettingPayload(BaseModel):
    """설정 하나를 쓰는 본문이며 수치 설정도 문자열로 실린다."""

    model_config = ConfigDict(extra="ignore")

    value: str = Field(min_length=1)


class ModelOption(BaseModel):
    """모델 설정에 고를 수 있는 값 하나다."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


def is_setting_key(value: str) -> bool:
    """사용자가 쓸 수 있는 키인지 답한다."""
    return value in SETTING_KEYS


def is_sensitive_setting_key(key: str) -> bool:
    """값 자체가 자격이라 저장할 때 암호화하고 조회할 때 가리는 키인지 답한다."""
    return key in _SENSITIVE_KEYS


def mask_setting_value(key: str, value: str) -> str:
    """민감한 키의 값은 끝 네 자만 남기고 가리며 그보다 짧으면 길이만 알린다."""
    if not is_sensitive_setting_key(key):
        return value
    if len(value) <= _VISIBLE_TAIL:
        return _MASK_CHARACTER * len(value)
    return f"{_MASK_CHARACTER * _MASK_WIDTH}{value[-_VISIBLE_TAIL:]}"


def setting_view(key: str, value: str, updated_at: datetime) -> dict[str, Any]:
    """저장된 설정을 창구가 내는 모양으로 성형한다."""
    return {
        "key": key,
        "maskedValue": mask_setting_value(key, value),
        "hasValue": True,
        "updatedAt": iso(updated_at),
    }
