"""chat 장기기억 저장소가 기억 API를 뒤에 두고 BaseStore 표면을 채우는지 검증한다."""

from __future__ import annotations

import httpx
import pytest
from langgraph.store.base import GetOp, SearchOp

from tests.support.chat_api import FakeChatMemoryApi
from tracer_agent.worker.agents.chat.memory import ChatMemoryClient
from tracer_agent.worker.agents.chat.store import MEMORY_NAMESPACE, ChatMemoryStore, ChatMemoryUnavailable

_BASE_URL = "http://tracer-api.test"


def _store(transport: httpx.MockTransport) -> tuple[ChatMemoryStore, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport)
    return ChatMemoryStore(ChatMemoryClient(http, _BASE_URL, "user-1", "scope-token")), http


def test_동기_배치는_비동기_그래프_밖의_사용을_막는다() -> None:
    store, _http = _store(httpx.MockTransport(FakeChatMemoryApi().handle))

    with pytest.raises(NotImplementedError):
        store.batch([])


def test_네임스페이스가_사용자를_담지_않는다() -> None:
    # 사용자 범위는 생성 시점에 묶인 진입점이 정하므로 네임스페이스가 사용자를 실어 나르지 않는다.
    assert MEMORY_NAMESPACE == ("chat_memory",)


async def test_열쇠_하나만_되읽고_없으면_비운다() -> None:
    api = FakeChatMemoryApi()
    api.facts["lang"] = "한국어를 쓴다"
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        found = await store.aget(MEMORY_NAMESPACE, "lang")
        missing = await store.aget(MEMORY_NAMESPACE, "tone")
        namespaces = await store.alist_namespaces()

    assert found is not None
    assert found.key == "lang"
    assert found.value == {"content": "한국어를 쓴다", "updatedAt": "2026-01-01T00:00:00.000Z"}
    assert missing is None
    assert namespaces == [MEMORY_NAMESPACE]


async def test_검색이_상한과_시작점을_지킨다() -> None:
    api = FakeChatMemoryApi()
    api.facts.update({"a": "1", "b": "2", "c": "3"})
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        window = await store.asearch(MEMORY_NAMESPACE, limit=2, offset=1)

    assert [item.key for item in window] == ["b", "c"]


async def test_질의어가_오면_그_말이_담긴_사실만_낸다() -> None:
    # 기억 API 는 검색을 제공하지 않으므로 받은 행 안에서 걸러야 질의어가 무시되지 않는다.
    api = FakeChatMemoryApi()
    api.facts.update({"lang": "한국어를 쓴다", "tone": "짧게 답한다"})
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        found = await store.asearch(MEMORY_NAMESPACE, query="한국어")

    assert [item.key for item in found] == ["lang"]


async def test_조건이_오면_그_조건을_만족하는_사실만_낸다() -> None:
    api = FakeChatMemoryApi()
    api.facts.update({"lang": "한국어를 쓴다", "tone": "짧게 답한다"})
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        found = await store.asearch(MEMORY_NAMESPACE, filter={"content": "짧게 답한다"})

    assert [item.key for item in found] == ["tone"]


async def test_다른_네임스페이스는_이_저장소가_답하지_않는다() -> None:
    api = FakeChatMemoryApi()
    api.facts["lang"] = "한국어를 쓴다"
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        found = await store.asearch(("other",))
        missing = await store.aget(("other",), "lang")

    assert found == []
    assert missing is None


async def test_한_배치의_읽기들이_조회_한_번을_나눠_갖는다() -> None:
    api = FakeChatMemoryApi()
    api.facts.update({"lang": "한국어를 쓴다", "tone": "짧게 답한다"})
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        results = await store.abatch(
            [
                GetOp(namespace=MEMORY_NAMESPACE, key="lang", refresh_ttl=False),
                SearchOp(
                    namespace_prefix=MEMORY_NAMESPACE,
                    filter=None,
                    limit=10,
                    offset=0,
                    query=None,
                    refresh_ttl=False,
                ),
            ]
        )

    assert api.reads == 1
    assert results[0] is not None
    assert len(results[1]) == 2


async def test_저장소는_되읽기만_하고_적재하지_않는다() -> None:
    api = FakeChatMemoryApi()
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        with pytest.raises(NotImplementedError):
            await store.aput(MEMORY_NAMESPACE, "lang", {"content": "한국어를 쓴다"})

    assert api.facts == {}


async def test_기억_API가_거절하면_상태_코드를_들고_끊는다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"ok": False})

    store, http = _store(httpx.MockTransport(handle))

    async with http:
        with pytest.raises(ChatMemoryUnavailable) as failed:
            await store.asearch(MEMORY_NAMESPACE)

    assert failed.value.status_code == 503
    assert "503" in str(failed.value)
