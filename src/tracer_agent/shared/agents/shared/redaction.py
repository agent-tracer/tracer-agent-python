"""가릴 것을 알아보는 낱말과 견주는 절차와 가린 자리에 넣는 표시를 계약에서 가져온다."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from string import ascii_letters, digits
from typing import Any

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
REDACTION_PATH = Path(__file__).resolve().parents[5] / "contract" / "agent" / "shared" / "redaction.json"

# 계약이 자격의 몸통에 허용한 글자는 영문자와 숫자와 세 구분자다.
_BODY_CHARACTERS = frozenset(ascii_letters + digits + "-_.")


class SuspectAction(StrEnum):
    """걸린 payload 를 자리마다 어떻게 하는지다."""

    DISCARD = "discard"
    REDACT = "redact"


class Inspected(StrEnum):
    """자리가 payload 의 어느 쪽을 보는지다."""

    KEYS = "keys"
    VALUES = "values"


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
    return tuple(_folded_key(str(word)) for word in _rules()[Inspected.KEYS]["words"])


@lru_cache(maxsize=1)
def _value_words() -> tuple[str, ...]:
    return tuple(_lower_ascii(str(word)) for word in _rules()[Inspected.VALUES]["words"])


@lru_cache(maxsize=1)
def _minimum_body_length() -> int:
    return int(_rules()[Inspected.VALUES]["requiresTrailingBody"]["minLength"])


@lru_cache(maxsize=1)
def _skips_space_between() -> bool:
    return bool(_rules()[Inspected.VALUES]["requiresTrailingBody"]["skipSpaceBetween"])


@lru_cache(maxsize=len(RedactionStage))
def _stage_rule(stage: RedactionStage) -> tuple[SuspectAction, frozenset[Inspected]]:
    declared: dict[str, Any] = _rules()["stages"][stage.value]
    return (
        SuspectAction(declared["onSuspect"]),
        frozenset(Inspected(name) for name in declared["inspects"]),
    )


def discards(stage: RedactionStage) -> bool:
    """이 자리가 걸린 payload 를 통째로 내보내지 않는지 낸다."""
    return _stage_rule(stage)[0] is SuspectAction.DISCARD


def inspects_keys(stage: RedactionStage) -> bool:
    """이 자리가 key 의 이름을 보는지 낸다."""
    return Inspected.KEYS in _stage_rule(stage)[1]


def inspects_values(stage: RedactionStage) -> bool:
    """이 자리가 값의 모양을 보는지 낸다."""
    return Inspected.VALUES in _stage_rule(stage)[1]


def _lower_ascii(text: str) -> str:
    """ASCII 영문자만 내려 접은 문자열의 자리가 원문의 자리와 하나씩 맞물린다."""
    return "".join(character.lower() if "A" <= character <= "Z" else character for character in text)


def _folded_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def is_suspect_key(key: str) -> bool:
    """영숫자만 남겨 접은 이름이 계약의 key 낱말을 품는지 견준다."""
    folded = _folded_key(key)
    return any(word in folded for word in _key_words())


def is_suspect_text(text: str) -> bool:
    """구분자를 지키고 접은 문자열에서 계약의 값 낱말 뒤에 자격의 몸통이 이어지는지 견준다."""
    folded = _lower_ascii(text)
    return any(_carries_body(folded, word) for word in _value_words())


def _carries_body(folded: str, word: str) -> bool:
    minimum = _minimum_body_length()
    start = folded.find(word)
    while start != -1:
        if _body_bounds(folded, start + len(word))[0] >= minimum:
            return True
        start = folded.find(word, start + 1)
    return False


def _body_bounds(folded: str, index: int) -> tuple[int, int]:
    """낱말 뒤에서 자격의 몸통이 차지하는 길이와 그 끝자리를 낸다."""
    if _skips_space_between():
        while index < len(folded) and folded[index] == " ":
            index += 1
    end = index
    while end < len(folded) and folded[end] in _BODY_CHARACTERS:
        end += 1
    return end - index, end


def _suspect_spans(folded: str) -> list[tuple[int, int]]:
    """낱말과 그 뒤의 몸통이 이룬 구간을 겹치지 않게 모아 앞에서부터 낸다."""
    minimum = _minimum_body_length()
    found: list[tuple[int, int]] = []
    for word in _value_words():
        start = folded.find(word)
        while start != -1:
            length, end = _body_bounds(folded, start + len(word))
            if length >= minimum:
                found.append((start, end))
            start = folded.find(word, start + 1)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(found):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _covered_text(text: str) -> str:
    """걸린 구간만 표시로 바꾸고 낱말 앞과 몸통 뒤의 본문은 그대로 남긴다."""
    folded = _lower_ascii(text)
    spans = _suspect_spans(folded)
    if not spans:
        return text
    kept: list[str] = []
    cursor = 0
    for start, end in spans:
        kept.append(text[cursor:start])
        kept.append(marker())
        cursor = end
    kept.append(text[cursor:])
    return "".join(kept)


def redact(value: RedactableValue, *, stage: RedactionStage) -> RedactableValue:
    """자리가 정한 대로 걸린 값만 표시로 바꾸거나 payload 를 통째로 폐기한다."""
    if discards(stage):
        _assert_clean(value, stage=stage, path="$")
        return value
    return _covered(value, stage=stage)


def redact_text(text: str, *, stage: RedactionStage) -> str:
    """경계를 넘는 문자열 하나를 자리의 규칙으로 견주어 걸리면 표시로 바꾼다."""
    if not (inspects_values(stage) and is_suspect_text(text)):
        return text
    if discards(stage):
        raise SuspectPayloadError("payload carries a credential-shaped value")
    return _covered_text(text)


def _covered(value: RedactableValue, *, stage: RedactionStage) -> RedactableValue:
    if isinstance(value, Mapping):
        return {key: _covered_entry(str(key), nested, stage=stage) for key, nested in value.items()}
    if isinstance(value, str):
        return _covered_text(value) if inspects_values(stage) else value
    if isinstance(value, int | float | bool) or value is None:
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
    if isinstance(value, str):
        if inspects_values(stage) and is_suspect_text(value):
            raise SuspectPayloadError(f"payload carries a credential-shaped value at {path}")
        return
    if isinstance(value, int | float | bool) or value is None:
        return
    if isinstance(value, Sequence):
        for index, nested in enumerate(value):
            _assert_clean(nested, stage=stage, path=f"{path}[{index}]")
        return
    raise SuspectPayloadError(f"payload carries an unsupported value at {path}")
