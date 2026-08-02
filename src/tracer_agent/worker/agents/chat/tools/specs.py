"""계약이 선언한 chat 도구 인자에서 모델이 볼 인자 모델을 만든다."""

from __future__ import annotations

import keyword
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, create_model

from tracer_agent.shared.agents.chat.tools.surface import (
    AGENT_READ_SURFACE,
    CONFIRM_SURFACE,
    MEMORY_SURFACE,
    READ_SURFACE,
    chat_tool_declarations,
    tool_names_on,
)
from tracer_agent.shared.agents.shared.models import TrimmedStr


def tool_arg_names(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """도구 하나의 인자 이름을 필수와 선택으로 나눈다."""
    args: Mapping[str, Mapping[str, Any]] = chat_tool_declarations()[name]["args"]
    required = tuple(arg for arg, declared in args.items() if declared["required"])
    optional = tuple(arg for arg, declared in args.items() if not declared["required"])
    return required, optional


READ_TOOL_NAMES: tuple[str, ...] = tool_names_on(READ_SURFACE)
# 확인 없이 되읽되 원장이 에이전트 서비스에 있어 기점이 다른 도구 이름이다.
AGENT_READ_TOOL_NAMES: tuple[str, ...] = tool_names_on(AGENT_READ_SURFACE)
MEMORY_TOOL_NAMES: tuple[str, ...] = tool_names_on(MEMORY_SURFACE)
WRITE_TOOL_NAMES: tuple[str, ...] = tool_names_on(CONFIRM_SURFACE)


def _enum_annotation(values: list[str]) -> Any:
    # 런타임 목록에서 만든 Literal이라 정적으로는 표현할 수 없어 Any로 넘긴다.
    return Literal[tuple(values)]


def _items_annotation(items: Mapping[str, Any]) -> Any:
    minimum = items.get("minLength")
    if minimum is None:
        return TrimmedStr
    return Annotated[TrimmedStr, Field(min_length=int(minimum))]


def _field(declared: Mapping[str, Any], alias: str | None) -> tuple[Any, Any]:
    kind = declared["type"]
    required = bool(declared["required"])
    described = str(declared["description"])
    if kind == "enum":
        annotation = _enum_annotation(declared["values"])
        if required:
            return annotation, Field(alias=alias, description=described)
        return annotation | None, Field(default=None, alias=alias, description=described)
    if kind == "integer":
        # 계약 default는 실행이 채우므로 스키마에는 상하한만 두고 생략을 허용한다.
        return int | None, Field(
            default=None,
            ge=int(declared["min"]),
            le=int(declared["max"]),
            alias=alias,
            description=described,
        )
    if kind == "array":
        annotation = list[_items_annotation(declared["items"])]  # type: ignore[misc]
        if required:
            return annotation, Field(max_length=int(declared["maxItems"]), alias=alias, description=described)
        return annotation | None, Field(
            default=None, max_length=int(declared["maxItems"]), alias=alias, description=described
        )
    if kind == "object":
        # 안쪽 키 구성은 계약이 정하지 않고 부르는 창구가 검증하므로 자유로운 객체로 받는다.
        if required:
            return dict[str, Any], Field(alias=alias, description=described)
        return dict[str, Any] | None, Field(default=None, alias=alias, description=described)
    minimum = int(declared.get("minLength") or 1)
    if required:
        return TrimmedStr, Field(min_length=minimum, alias=alias, description=described)
    return TrimmedStr | None, Field(default=None, min_length=minimum, alias=alias, description=described)


def build_args_model(name: str) -> type[Any]:
    """도구 하나의 인자를 계약이 선언한 순서와 타입 그대로 갖는 Pydantic 모델을 만든다."""
    fields: dict[str, Any] = {}
    for arg, declared in chat_tool_declarations()[name]["args"].items():
        # from 같은 파이썬 예약어는 별칭으로 와이어 이름을 유지하고 안전한 필드명으로 담는다.
        key = f"{arg}_" if keyword.iskeyword(arg) else arg
        fields[key] = _field(declared, arg if key != arg else None)
    model_name = "".join(part.capitalize() for part in name.split("_")) + "Args"
    model: type[Any] = create_model(
        model_name, __config__=ConfigDict(extra="forbid", populate_by_name=True), **fields
    )
    return model


ARGS_MODELS: dict[str, type[Any]] = {name: build_args_model(name) for name in chat_tool_declarations()}
