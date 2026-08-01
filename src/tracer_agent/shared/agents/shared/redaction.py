"""가릴 것을 알아보는 낱말과 견주는 절차와 가린 자리에 넣는 표시를 계약에서 가져온다."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
REDACTION_PATH = Path(__file__).resolve().parents[5] / "contract" / "agent" / "shared" / "redaction.json"

_DISCARD = "discard"
_KEYS = "keys"

type RedactableScalar = str | int | float | bool | None
type RedactableValue = RedactableScalar | Mapping[str, RedactableValue] | Sequence[RedactableValue]


class RedactionStage(StrEnum):
    """가리는 절차가 걸리는 자리이며 자리마다 걸린 것을 어떻게 하는지가 다르다."""

    TRACE = "trace"
    QUERY = "query"
    OUTPUT = "output"


class SuspectPayloadError(ValueError):
    """폐기하는 자리가 가려야 할 것을 만나 payload 를 통째로 내보내지 않는다."""


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(REDACTION_PATH.read_text(encoding="utf-8"))
    return document


@lru_cache(maxsize=1)
def marker() -> str:
    """가린 자리를 통째로 대신하며 값의 길이도 모양도 남기지 않는 문자열이다."""
    return str(_rules()["marker"])


@lru_cache(maxsize=1)
def _key_words() -> tuple[str, ...]:
    return tuple(_folded_key(str(word)) for word in _rules()[_KEYS]["words"])


@lru_cache(maxsize=len(RedactionStage))
def _stage_rule(stage: RedactionStage) -> tuple[str, frozenset[str]]:
    declared: dict[str, Any] = _rules()["stages"][stage.value]
    return str(declared["onSuspect"]), frozenset(str(name) for name in declared["inspects"])


def discards(stage: RedactionStage) -> bool:
    """이 자리가 걸린 payload 를 통째로 내보내지 않는지 낸다."""
    return _stage_rule(stage)[0] == _DISCARD


def inspects_keys(stage: RedactionStage) -> bool:
    """이 자리가 key 의 이름을 보는지 낸다."""
    return _KEYS in _stage_rule(stage)[1]


def _folded_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def is_suspect_key(key: str) -> bool:
    """영숫자만 남겨 접은 이름이 계약의 key 낱말을 품는지 견준다."""
    folded = _folded_key(key)
    return any(word in folded for word in _key_words())


def redact(value: RedactableValue, *, stage: RedactionStage) -> RedactableValue:
    """자리가 정한 대로 걸린 값만 표시로 바꾸거나 payload 를 통째로 폐기한다."""
    if discards(stage):
        _assert_clean(value, stage=stage, path="$")
        return value
    return _covered(value, stage=stage)


def _covered(value: RedactableValue, *, stage: RedactionStage) -> RedactableValue:
    if isinstance(value, Mapping):
        return {key: _covered_entry(str(key), nested, stage=stage) for key, nested in value.items()}
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Sequence):
        return [_covered(nested, stage=stage) for nested in value]
    return marker()


def _covered_entry(key: str, value: RedactableValue, *, stage: RedactionStage) -> RedactableValue:
    if inspects_keys(stage) and is_suspect_key(key):
        return marker()
    return _covered(value, stage=stage)


def _assert_clean(value: RedactableValue, *, stage: RedactionStage, path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if inspects_keys(stage) and is_suspect_key(str(key)):
                raise SuspectPayloadError(f"payload carries a credential name at {path}.{key}")
            _assert_clean(nested, stage=stage, path=f"{path}.{key}")
        return
    if isinstance(value, str | int | float | bool) or value is None:
        return
    if isinstance(value, Sequence):
        for index, nested in enumerate(value):
            _assert_clean(nested, stage=stage, path=f"{path}[{index}]")
        return
    raise SuspectPayloadError(f"payload carries an unsupported value at {path}")
