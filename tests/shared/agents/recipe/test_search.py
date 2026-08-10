"""레시피 색인의 질의 조건과 상대 점수 절단을 검증한다(색인 없음)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.support.contract import wire_contract
from tracer_agent.shared.agents.recipe.index import recipes_index_alias
from tracer_agent.shared.agents.recipe.search import (
    MATCH_FIELDS,
    MINIMUM_SHOULD_MATCH,
    RELATIVE_SCORE_CUTOFF_RATIO,
    OpenSearchClient,
    OpenSearchIndexWriter,
    OpenSearchRecipeSearch,
    apply_relative_cutoff,
)
from tracer_agent.shared.agents.shared.axis import AGENT_BACKEND

_QUERY = wire_contract("search.index.json")["indices"]["recipes"]["query"]


def _client(handler: Any) -> tuple[OpenSearchClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(handle)
    return OpenSearchClient(httpx.AsyncClient(transport=transport), "http://opensearch"), seen


def test_질의_조건이_계약이_선언한_값과_같다() -> None:
    assert _QUERY["minimumShouldMatch"] == MINIMUM_SHOULD_MATCH
    assert _QUERY["relativeScoreCutoffRatio"] == RELATIVE_SCORE_CUTOFF_RATIO
    assert list(MATCH_FIELDS) == _QUERY["matchFields"]


def test_가장_높은_점수의_비율에_못_미치는_적중을_버린다() -> None:
    hits = [{"_score": 10.0}, {"_score": 4.0}, {"_score": 3.9}]

    assert apply_relative_cutoff(hits) == [{"_score": 10.0}, {"_score": 4.0}]


def test_점수가_없으면_아무것도_버리지_않는다() -> None:
    hits = [{"_id": "r1"}, {"_id": "r2"}]

    assert apply_relative_cutoff(hits) == hits


async def test_이_축이_색인한_채택된_자기_레시피만_대상으로_질의한다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": {"hits": []}})

    client, seen = _client(handle)

    await OpenSearchRecipeSearch(client).search("user-1", "질의", 5)

    body = json.loads(seen[0].read())
    assert seen[0].url.path == f"/{recipes_index_alias()}/_search"
    assert body["size"] == 5
    assert body["query"]["bool"]["filter"] == [
        {"term": {"userId": "user-1"}},
        {"term": {"backend": AGENT_BACKEND}},
        {"term": {"status": "active"}},
    ]


async def test_적중을_얇은_모양으로_옮긴다() -> None:
    # 문서 식별자에 축이 붙었으므로 레시피의 식별자는 문서의 recipeId 칸에서 온다.
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_id": f"{AGENT_BACKEND}:r1",
                            "_score": 2.0,
                            "_source": {
                                "recipeId": "r1",
                                "title": "제목",
                                "intent": "의도",
                                "description": "설명",
                                "useWhen": ["조건", ""],
                            },
                        }
                    ]
                }
            },
        )

    client, _ = _client(handle)

    hits = await OpenSearchRecipeSearch(client).search("user-1", "질의", 3)

    assert [(one.id, one.title, one.use_when, one.score) for one in hits] == [("r1", "제목", ["조건"], 2.0)]


async def test_이미_없는_문서를_지우는_것은_실패로_세지_않는다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"result": "not_found"})

    client, _ = _client(handle)

    await OpenSearchIndexWriter(client).delete_document(recipes_index_alias(), "r1")


async def test_색인이_거절하면_부른_쪽이_다시_시도할_수_있게_올린다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client, _ = _client(handle)

    with pytest.raises(Exception, match="opensearch"):
        await OpenSearchIndexWriter(client).index_document(recipes_index_alias(), "r1", {})
