"""체크포인트에 실리는 타입을 밝혀 되살릴 것만 되살리는 직렬화기를 낸다."""

from __future__ import annotations

from typing import Any, get_args, get_type_hints

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from ....shared.agents.chat.models import ChatState
from ....shared.agents.recipe_scan.models import ProbeDispatch, RecipeScanState
from ....shared.agents.task_cleanup.models import InspectDispatch, TaskCleanupState
from ....shared.agents.title_suggestion.models import TitleSuggestionState

# 그래프의 상태 칸과 팬아웃이 실어 보내는 짐이 체크포인트에 적히는 값의 전부다.
_CHECKPOINTED_ROOTS: tuple[type, ...] = (
    ChatState,
    RecipeScanState,
    TaskCleanupState,
    TitleSuggestionState,
    ProbeDispatch,
    InspectDispatch,
)


def _annotations(owner: type) -> list[Any]:
    if isinstance(owner, type) and issubclass(owner, BaseModel):
        return [field.annotation for field in owner.model_fields.values()]
    return list(get_type_hints(owner, include_extras=True).values())


def _walk(annotation: Any, found: set[type[BaseModel]]) -> None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in found:
            return
        found.add(annotation)
        for nested in _annotations(annotation):
            _walk(nested, found)
        return
    for argument in get_args(annotation):
        _walk(argument, found)


def checkpointed_models() -> frozenset[type[BaseModel]]:
    """상태와 팬아웃 짐에서 닿을 수 있는 모델이며 체크포인트가 이것들을 담는다."""
    found: set[type[BaseModel]] = set()
    for root in _CHECKPOINTED_ROOTS:
        if isinstance(root, type) and issubclass(root, BaseModel):
            _walk(root, found)
            continue
        for annotation in _annotations(root):
            _walk(annotation, found)
    return frozenset(found)


def graph_serde() -> JsonPlusSerializer:
    """밝힌 타입만 되살리므로 체크포인트가 임의의 클래스를 만들어내지 않는다."""
    return JsonPlusSerializer(allowed_msgpack_modules=sorted(checkpointed_models(), key=repr))
