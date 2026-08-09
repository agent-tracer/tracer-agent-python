"""recipes 색인의 정의가 계약과 같은지와 이미 있을 때 넘어가는지 검증한다(색인 없음)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.support.contract import wire_contract
from tracer_agent.shared.agents.recipe.index import (
    recipes_index_alias,
    recipes_index_definition,
)
from tracer_agent.shared.agents.recipe.search import (
    OpenSearchClient,
    OpenSearchIndexAdmin,
    OpenSearchRejected,
)

_DECLARED = wire_contract("search.index.json")["indices"]["recipes"]
_ANALYSIS = _DECLARED["settings"]["analysis"]

# 색인이 없다고 답하는 상태와 본문이며 OpenSearch 는 없는 자리에 이 상태를 낸다.
_MISSING = (404, {"error": {"type": "index_not_found_exception"}})
_CREATED = (200, {"acknowledged": True})

Reply = tuple[int, object]


def _client(handler: Any) -> tuple[OpenSearchClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, payload = handler(request)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handle)
    return OpenSearchClient(httpx.AsyncClient(transport=transport), "http://opensearch"), seen


def _routed(**replies: Reply) -> Any:
    def handle(request: httpx.Request) -> Reply:
        if request.method == "PUT":
            return replies.get("create", _CREATED)
        if request.url.path.startswith("/_alias/"):
            return replies.get("alias", _MISSING)
        return replies.get("index", _MISSING)

    return handle


class Test정의가_계약이_선언한_값과_같다:
    def test_별칭과_물리_인덱스_이름이_계약과_같다(self) -> None:
        definition = recipes_index_definition()

        assert definition.alias == _DECLARED["alias"]
        assert definition.index == _DECLARED["index"]

    def test_검색과_배출이_쓰는_별칭이_계약이_선언한_별칭이다(self) -> None:
        assert recipes_index_alias() == _DECLARED["alias"]

    def test_설정이_계약의_샤드_수와_분석기와_토크나이저를_그대로_싣는다(self) -> None:
        settings = recipes_index_definition().settings

        assert settings["number_of_shards"] == _DECLARED["settings"]["number_of_shards"]
        assert settings["number_of_replicas"] == _DECLARED["settings"]["number_of_replicas"]
        assert settings["analysis"] == {
            "analyzer": _ANALYSIS["analyzer"],
            "tokenizer": _ANALYSIS["tokenizer"],
        }

    def test_매핑의_칸과_종류와_분석기가_계약이_선언한_것과_같다(self) -> None:
        properties = recipes_index_definition().mappings["properties"]
        declared = _DECLARED["document"]["fields"]

        assert properties == {
            name: {key: field[key] for key in ("type", "analyzer") if key in field}
            for name, field in declared.items()
        }

    def test_계약이_산문으로_적은_형식을_매핑에_싣지_않는다(self) -> None:
        properties = recipes_index_definition().mappings["properties"]

        assert properties["updatedAt"] == {"type": "date"}


class Test색인이_없을_때만_세운다:
    async def test_색인이_없으면_계약이_정한_본문으로_세우고_별칭을_건다(self) -> None:
        client, seen = _client(_routed())
        definition = recipes_index_definition()

        await OpenSearchIndexAdmin(client).ensure_index(definition)

        created = next(request for request in seen if request.method == "PUT")
        assert created.url.path == f"/{definition.index}"
        assert json.loads(created.read()) == {
            "settings": definition.settings,
            "mappings": definition.mappings,
            "aliases": {definition.alias: {}},
        }

    async def test_색인이_이미_있으면_다시_세우지_않는다(self) -> None:
        client, seen = _client(_routed(index=(200, {"recipes-v2": {}})))

        await OpenSearchIndexAdmin(client).ensure_index(recipes_index_definition())

        assert [request.method for request in seen] == ["GET"]

    async def test_별칭이_이미_붙어_있으면_새_색인에_다시_걸지_않는다(self) -> None:
        client, seen = _client(_routed(alias=(200, {"recipes-v1": {"aliases": {}}})))

        await OpenSearchIndexAdmin(client).ensure_index(recipes_index_definition())

        created = next(request for request in seen if request.method == "PUT")
        assert "aliases" not in json.loads(created.read())

    async def test_다른_축이_같은_순간에_세웠으면_실패로_세지_않는다(self) -> None:
        rejected = (400, {"error": {"type": "resource_already_exists_exception"}})
        client, _ = _client(_routed(create=rejected))

        await OpenSearchIndexAdmin(client).ensure_index(recipes_index_definition())

    async def test_다른_사유로_거절하면_기동이_그_사실을_안다(self) -> None:
        rejected = (400, {"error": {"type": "mapper_parsing_exception"}})
        client, _ = _client(_routed(create=rejected))

        with pytest.raises(OpenSearchRejected):
            await OpenSearchIndexAdmin(client).ensure_index(recipes_index_definition())

    async def test_색인_조회가_다른_사유로_실패하면_세우려_들지_않는다(self) -> None:
        client, seen = _client(_routed(index=(503, {"error": {"type": "unavailable"}})))

        with pytest.raises(OpenSearchRejected):
            await OpenSearchIndexAdmin(client).ensure_index(recipes_index_definition())

        assert [request.method for request in seen] == ["GET"]
