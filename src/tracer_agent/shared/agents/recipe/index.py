"""recipes 색인의 별칭과 물리 이름과 설정과 매핑을 계약에서 읽는다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from ..shared.contract_root import CONTRACT_ROOT
from ..shared.json_view import JsonObject, JsonValue, as_object

_SEARCH_INDEX_PATH = CONTRACT_ROOT / "wire" / "search.index.json"

# 계약이 사람에게 뜻을 적는 칸이며 색인은 이 이름의 칸을 설정으로 받지 않는다.
PROSE_KEY = "meaning"

# 색인 매핑이 칸 하나에서 받는 선언이며 계약의 나머지 칸은 사람이 읽는 근거다.
MAPPING_KEYS = ("type", "analyzer")


@dataclass(frozen=True)
class SearchIndexDefinition:
    """색인 하나를 세우는 데 필요한 값 전부이며 그 값의 정본은 계약이다."""

    alias: str
    index: str
    settings: JsonObject
    mappings: JsonObject


class SearchIndexAdminPort(Protocol):
    """선언한 색인이 없으면 세우는 창구다."""

    async def ensure_index(self, definition: SearchIndexDefinition) -> None:
        """색인이 이미 있으면 아무것도 하지 않는다."""
        ...


@lru_cache(maxsize=1)
def _recipes() -> dict[str, Any]:
    declared = json.loads(_SEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    index: dict[str, Any] = declared["indices"]["recipes"]
    return index


def recipes_index_alias() -> str:
    """검색과 배출이 문서를 읽고 쓰는 이름이며 물리 인덱스는 이 별칭 뒤에 선다."""
    return str(_recipes()["alias"])


def recipes_index_definition() -> SearchIndexDefinition:
    """계약이 선언한 값만으로 recipes 색인의 정의를 만든다."""
    declared = _recipes()
    return SearchIndexDefinition(
        alias=str(declared["alias"]),
        index=str(declared["index"]),
        settings=as_object(_without_prose(declared["settings"])),
        mappings={"properties": _properties(declared["document"]["fields"])},
    )


def _properties(fields: dict[str, Any]) -> JsonObject:
    return {name: _property(declared) for name, declared in fields.items()}


def _property(declared: dict[str, Any]) -> JsonObject:
    return {key: declared[key] for key in MAPPING_KEYS if key in declared}


def _without_prose(value: JsonValue) -> JsonValue:
    """계약이 뜻을 적은 칸을 제외한 나머지가 색인이 받는 설정이다."""
    if isinstance(value, dict):
        return {key: _without_prose(item) for key, item in value.items() if key != PROSE_KEY}
    if isinstance(value, list):
        return [_without_prose(item) for item in value]
    return value
