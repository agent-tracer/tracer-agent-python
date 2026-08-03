"""경계를 넘는 JSON 값의 타입과 그 값을 좁히는 방법을 소유한다."""

from __future__ import annotations

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]


class MalformedJson(ValueError):
    """경계가 약속한 모양과 다른 값이 왔다."""


def as_object(value: JsonValue) -> JsonObject:
    """객체 하나를 기대한 자리에서 그 객체를 낸다."""
    if not isinstance(value, dict):
        raise MalformedJson(f"expected an object but got {type(value).__name__}")
    return value


def as_objects(value: JsonValue) -> list[JsonObject]:
    """객체 목록을 기대한 자리에서 객체가 아닌 항목을 버리고 낸다."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise MalformedJson(f"expected a list but got {type(value).__name__}")
    return [item for item in value if isinstance(item, dict)]


def text_list(value: JsonValue) -> list[str]:
    """문자열 목록을 기대한 자리에서 문자열이 아닌 항목을 버리고 낸다."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise MalformedJson(f"expected a list but got {type(value).__name__}")
    return [item for item in value if isinstance(item, str)]


def as_int(value: JsonValue, default: int = 0) -> int:
    """정수 하나를 기대한 자리에서 값이 수일 때만 그 정수를 내고 아니면 기본값을 쓴다."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return int(value)


def text(value: JsonValue) -> str:
    """문자열 하나를 기대한 자리에서 그 문자열을 낸다."""
    if not isinstance(value, str):
        raise MalformedJson(f"expected a string but got {type(value).__name__}")
    return value


def opt_text(value: JsonValue) -> str | None:
    """비어 있을 수 있는 문자열 자리에서 값이 문자열일 때만 낸다."""
    return value if isinstance(value, str) else None
