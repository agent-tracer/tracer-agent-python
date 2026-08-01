"""계약이 선언한 chat 도구 인자에서 모델이 볼 인자 모델을 만든다."""

from __future__ import annotations

import json
import keyword
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, create_model

from tracer_agent.shared.agents.chat.tools.bindings import TOOL_BINDINGS
from tracer_agent.shared.agents.shared.models import TrimmedStr

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
CHAT_TOOLS_PATH = Path(__file__).resolve().parents[6] / "contract" / "agent" / "chat" / "tool.json"

MEMORY_TOOL_NAMES: tuple[str, ...] = ("recall_facts", "remember_fact")


@lru_cache(maxsize=1)
def chat_tool_declarations() -> Mapping[str, Mapping[str, Any]]:
    """도구 이름마다 mutation 여부와 인자 선언을 낸다."""
    declared = json.loads(CHAT_TOOLS_PATH.read_text(encoding="utf-8"))["tools"]
    return {str(name): dict(tool) for name, tool in declared.items()}


def tool_arg_names(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """도구 하나의 인자 이름을 필수와 선택으로 나눈다."""
    args: Mapping[str, Mapping[str, Any]] = chat_tool_declarations()[name]["args"]
    required = tuple(arg for arg, declared in args.items() if declared["required"])
    optional = tuple(arg for arg, declared in args.items() if not declared["required"])
    return required, optional


def _is_agent_owned(name: str) -> bool:
    """도구가 부르는 경로가 추적이 아니라 에이전트 서비스 자신의 것인지를 가른다."""
    return TOOL_BINDINGS[name].path.startswith("/api/agent/")


def _is_mutation(name: str) -> bool:
    return bool(chat_tool_declarations()[name]["mutation"])


def _read_names(agent_owned: bool) -> tuple[str, ...]:
    return tuple(
        name
        for name in chat_tool_declarations()
        if not _is_mutation(name) and name not in MEMORY_TOOL_NAMES and _is_agent_owned(name) is agent_owned
    )


READ_TOOL_NAMES: tuple[str, ...] = _read_names(agent_owned=False)
# 게이트 없이 되읽되 원장이 에이전트 서비스에 있어 기점이 다른 도구 이름이다.
AGENT_READ_TOOL_NAMES: tuple[str, ...] = _read_names(agent_owned=True)
WRITE_TOOL_NAMES: tuple[str, ...] = tuple(name for name in chat_tool_declarations() if _is_mutation(name))


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
    # 모양을 계약이 정하지 않는 object 인자는 JSON 본문을 문자열로 받고 부르는 창구가 그 모양을 검증한다.
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
