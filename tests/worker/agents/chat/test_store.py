"""chat 장기기억 저장소가 기억 API를 뒤에 두고 BaseStore 표면을 채우는지 검증한다."""

from __future__ import annotations

import httpx
import pytest

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


async def test_적재가_기억_API에_그대로_흐른다() -> None:
    api = FakeChatMemoryApi()
    store, http = _store(httpx.MockTransport(api.handle))

    async with http:
        await store.aput(MEMORY_NAMESPACE, "lang", {"content": "한국어를 쓴다"})

    assert api.facts == {"lang": "한국어를 쓴다"}


async def test_기억_API가_거절하면_상태_코드를_들고_끊는다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"ok": False})

    store, http = _store(httpx.MockTransport(handle))

    async with http:
        with pytest.raises(ChatMemoryUnavailable) as failed:
            await store.asearch(MEMORY_NAMESPACE)

    assert failed.value.status_code == 503
    assert "503" in str(failed.value)
