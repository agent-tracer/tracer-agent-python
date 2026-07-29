"""검색 색인 클라이언트의 생성을 소유한다."""

from __future__ import annotations

from opensearchpy import AsyncOpenSearch


def create_search_client(node: str) -> AsyncOpenSearch:
    """색인을 읽는 비동기 클라이언트를 만든다."""
    return AsyncOpenSearch(hosts=[node])
