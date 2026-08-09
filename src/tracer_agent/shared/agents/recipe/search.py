"""레시피 색인을 세우는 일과 질의와 문서 쓰기를 OpenSearch 창구로 구현한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from ..shared.json_view import JsonObject, JsonValue, as_objects, text_list
from .index import SearchIndexDefinition, recipes_index_alias
from .models import RECIPE_STATUS_ACTIVE

# 얇은 코퍼스에서 실측해 정한 값이며 계약의 wire/search.index.json 이 같은 수를 갖는다.
MINIMUM_SHOULD_MATCH = "30%"

# 계약의 relativeScoreCutoffRatio 와 같은 값이며 이 비율에 못 미치는 적중을 버린다.
RELATIVE_SCORE_CUTOFF_RATIO = 0.4

# 계약이 선언한 뒤질 칸이며 색인 문서가 갖지 않는 칸을 여기에 적으면 언제나 비어서 온다.
MATCH_FIELDS = ("title", "intent", "description", "useWhen", "summaryMd")

_NOT_FOUND_STATUS = 404
_CLIENT_ERROR = 400

# 다른 축이 같은 색인을 먼저 세웠을 때 색인이 내는 사유다.
_ALREADY_EXISTS = "resource_already_exists_exception"


@dataclass(frozen=True)
class RecipeSearchHit:
    """색인이 낸 레시피 한 건이며 점수는 적중 사이의 순서에만 뜻이 있다."""

    id: str
    title: str
    intent: str
    description: str
    use_when: list[str]
    score: float


class RecipeSearchPort(Protocol):
    """레시피 색인의 질의를 제공하며 쓰기는 아웃박스 배출기가 맡는다."""

    async def search(self, user_id: str, q: str, limit: int) -> list[RecipeSearchHit]:
        """채택된 자기 레시피만 대상으로 점수가 높은 것부터 낸다."""
        ...


class SearchIndexWriterPort(Protocol):
    """검색 색인에 문서를 덮어쓰거나 지우는 창구다."""

    async def index_document(self, alias: str, document_id: str, document: JsonObject) -> None:
        """문서 하나를 식별자로 덮어쓴다."""
        ...

    async def delete_document(self, alias: str, document_id: str) -> None:
        """문서 하나를 지운다."""
        ...


class OpenSearchClient:
    """색인을 부르는 최소 전송이며 색인 클라이언트 의존성을 더하지 않으려고 HTTP 를 그대로 쓴다."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def request(self, method: str, path: str, body: JsonValue = None) -> JsonValue:
        """2xx 가 아니면 부른 쪽이 재시도할 수 있게 예외를 낸다."""
        response = await self._client.request(method, f"{self._base_url}{path}", json=body)
        if response.status_code >= _CLIENT_ERROR:
            raise OpenSearchRejected(
                response.status_code,
                f"opensearch {method} {path} responded {response.status_code}",
                response.text,
            )
        if not response.text:
            return None
        parsed: JsonValue = response.json()
        return parsed


class OpenSearchRejected(Exception):
    """색인이 요청을 받아들이지 않았으며 상태와 본문을 함께 싣는다."""

    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class OpenSearchRecipeSearch:
    """채택된 자기 레시피만 대상으로 본문 유사도를 질의한다."""

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client

    async def search(self, user_id: str, q: str, limit: int) -> list[RecipeSearchHit]:
        """색인이 매긴 점수가 높은 것부터 상대 절단을 지나 남은 적중을 낸다."""
        body: dict[str, Any] = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": q,
                                "fields": list(MATCH_FIELDS),
                                "minimum_should_match": MINIMUM_SHOULD_MATCH,
                            }
                        }
                    ],
                    "filter": [
                        {"term": {"userId": user_id}},
                        {"term": {"status": RECIPE_STATUS_ACTIVE}},
                    ],
                }
            },
        }
        payload = await self._client.request("POST", f"/{recipes_index_alias()}/_search", body)
        return [_to_hit(hit) for hit in apply_relative_cutoff(_hits(payload))]


class OpenSearchIndexWriter:
    """배출기가 낸 색인 쓰기를 원장의 식별자를 문서 식별자로 삼아 수행한다."""

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client

    async def index_document(self, alias: str, document_id: str, document: JsonObject) -> None:
        """문서 식별자를 경로에 안전하게 실어 색인에 덮어쓴다."""
        await self._client.request("PUT", f"/{alias}/_doc/{quote(document_id, safe='')}", document)

    async def delete_document(self, alias: str, document_id: str) -> None:
        """이미 없는 문서를 지우는 것은 지운 상태에 이르렀다는 뜻이므로 실패로 세지 않는다."""
        try:
            await self._client.request("DELETE", f"/{alias}/_doc/{quote(document_id, safe='')}")
        except OpenSearchRejected as rejected:
            if rejected.status != _NOT_FOUND_STATUS:
                raise


class OpenSearchIndexAdmin:
    """계약이 선언한 설정과 매핑으로 색인을 세우고 그 색인에 별칭을 건다."""

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client

    async def ensure_index(self, definition: SearchIndexDefinition) -> None:
        """두 축이 같은 순간에 세워도 색인이 하나만 서게 한다."""
        if await self._found("GET", f"/{definition.index}"):
            return
        body: JsonObject = {"settings": definition.settings, "mappings": definition.mappings}
        # 별칭 하나가 인덱스 둘을 가리키면 쓰기와 교체가 막히므로 이미 쓰인 별칭을 다시 걸지 않는다.
        if not await self._found("GET", f"/_alias/{definition.alias}"):
            body["aliases"] = {definition.alias: {}}
        try:
            await self._client.request("PUT", f"/{definition.index}", body)
        except OpenSearchRejected as rejected:
            # 다른 축이 먼저 세웠으면 세우려던 상태에 이미 이르렀다.
            if _ALREADY_EXISTS not in rejected.body:
                raise

    async def _found(self, method: str, path: str) -> bool:
        """색인이 그 자리를 안다고 답하면 참을 내고 없다고 답하면 거짓을 낸다."""
        try:
            await self._client.request(method, path)
        except OpenSearchRejected as rejected:
            if rejected.status == _NOT_FOUND_STATUS:
                return False
            raise
        return True


def apply_relative_cutoff(hits: list[JsonObject]) -> list[JsonObject]:
    """상한을 채우려고 관련 없는 것을 함께 내지 않는다."""
    top_score = _score(hits[0]) if hits else 0.0
    if top_score <= 0:
        return hits
    threshold = top_score * RELATIVE_SCORE_CUTOFF_RATIO
    return [hit for hit in hits if _score(hit) >= threshold]


def _hits(payload: JsonValue) -> list[JsonObject]:
    if not isinstance(payload, dict):
        return []
    found = payload.get("hits")
    if not isinstance(found, dict):
        return []
    return as_objects(found.get("hits"))


def _score(hit: JsonObject) -> float:
    score = hit.get("_score")
    return float(score) if isinstance(score, int | float) and not isinstance(score, bool) else 0.0


def _to_hit(hit: JsonObject) -> RecipeSearchHit:
    source = hit.get("_source")
    fields: JsonObject = source if isinstance(source, dict) else {}
    return RecipeSearchHit(
        id=str(hit.get("_id", "")),
        title=_string(fields.get("title")),
        intent=_string(fields.get("intent")),
        description=_string(fields.get("description")),
        use_when=[one for one in text_list(fields.get("useWhen")) if one],
        score=_score(hit),
    )


def _string(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""
