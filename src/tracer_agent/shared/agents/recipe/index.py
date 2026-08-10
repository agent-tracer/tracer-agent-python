"""recipes 색인과 그 색인까지 가는 배출 단계의 값을 계약에서 읽는다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from ..shared.axis import AGENT_BACKEND
from ..shared.contract_root import CONTRACT_ROOT
from ..shared.json_view import JsonObject, as_object

_SEARCH_INDEX_PATH = CONTRACT_ROOT / "wire" / "search.index.json"

# 계약이 색인 반영 단계 가운데 아웃박스를 소진하는 단계에 붙인 이름이다.
_DRAIN_STAGE = "drain"

# 계약이 배출 주기를 밀리초로 적고 이 구현은 초로 기다린다.
_MILLIS_PER_SECOND = 1000.0


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
def _declared() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_SEARCH_INDEX_PATH.read_text(encoding="utf-8"))
    return payload


def _recipes() -> dict[str, Any]:
    index: dict[str, Any] = _declared()["indices"]["recipes"]
    return index


def _drain() -> dict[str, Any]:
    stages: list[dict[str, Any]] = _declared()["pipeline"]["stages"]
    return next(stage for stage in stages if stage["name"] == _DRAIN_STAGE)


def recipes_index_alias() -> str:
    """검색과 배출이 문서를 읽고 쓰는 이름이며 물리 인덱스는 이 별칭 뒤에 선다."""
    return str(_recipes()["alias"])


def recipes_index_definition() -> SearchIndexDefinition:
    """계약이 선언한 값만으로 recipes 색인의 정의를 만든다."""
    declared = _recipes()
    return SearchIndexDefinition(
        alias=str(declared["alias"]),
        index=str(declared["index"]),
        settings=as_object(declared["settings"]),
        mappings={"properties": _properties(declared["document"]["fields"])},
    )


def recipe_document_id(recipe_id: str) -> str:
    """색인 문서 하나를 가리키는 식별자이며 축과 레시피 식별자를 계약의 template 으로 잇는다."""
    declared = _recipes()["documentId"]
    with_axis = str(declared["template"]).replace(str(declared["placeholder"]), AGENT_BACKEND)
    return with_axis.format(recipeId=recipe_id)


def recipes_index_bootstrap_steps() -> tuple[str, ...]:
    """색인을 세우는 절차이며 두 축이 같은 순간에 세워도 한쪽이 실패하지 않게 한다."""
    return tuple(str(step) for step in _recipes()["bootstrap"]["steps"])


def projector_startup_order() -> tuple[str, ...]:
    """배경 작업을 시작하는 순서이며 색인이 선 뒤에 문서를 쓰게 한다."""
    return tuple(str(step) for step in _recipes()["bootstrap"]["startupOrder"])


def search_outbox_batch_size() -> int:
    """배출 한 번이 읽는 아웃박스 행의 수다."""
    return int(_drain()["batchSize"])


def search_outbox_drain_interval_s() -> float:
    """배출을 다시 부르기까지 기다리는 시간이다."""
    return float(_drain()["intervalMs"]) / _MILLIS_PER_SECOND


def search_outbox_drain_lock_key() -> int:
    """배출기가 잡는 자문 잠금 열쇠이며 축마다 다른 값이라 두 축이 서로를 기다리지 않는다."""
    declared = _drain()["advisoryLock"]
    return int(declared["base"]) + int(declared["axisOffset"][AGENT_BACKEND])


def _mapping_keys() -> tuple[str, ...]:
    """색인 매핑이 칸 하나에서 받는 선언의 이름이며 나머지는 사람이 읽는 산문이다."""
    return tuple(str(key) for key in _declared()["bodyRule"]["mappingKeys"])


def _properties(fields: dict[str, Any]) -> JsonObject:
    keys = _mapping_keys()
    return {name: _property(declared, keys) for name, declared in fields.items()}


def _property(declared: dict[str, Any], keys: tuple[str, ...]) -> JsonObject:
    return {key: declared[key] for key in keys if key in declared}
