"""chat 장기기억을 기억 API 뒤에 둔 LangGraph BaseStore로 소유한다."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)

from .memory import ChatMemoryClient, ChatMemoryResult

# 장기기억은 스레드를 가로지르고 사용자 범위는 생성 시점에 묶이므로 네임스페이스가 사용자를 담지 않는다.
MEMORY_NAMESPACE: tuple[str, ...] = ("chat_memory",)

# 기억 API가 사실 목록을 싣는 필드다.
FACTS_FIELD = "facts"

# 기억 API 행에서 사실 하나를 여는 열쇠 필드이며 나머지 필드는 그대로 값에 실린다.
KEY_FIELD = "key"

# 기억 API에 적재할 내용을 싣는 값 필드다.
CONTENT_FIELD = "content"

# 기억 API가 적재를 알리는 상태 필드와 값이며 두 백엔드가 같은 문자열을 모델에게 보인다.
STATUS_FIELD = "status"
REMEMBERED_STATUS = "remembered"

# 기억 API는 사용자 사실 전체를 한 번에 돌려주므로 검색은 그 전체를 덮는 상한으로 부른다.
RECALL_LIMIT = 1000

# 기억 API 행은 갱신 시각을 제 필드로 싣고 다니므로 저장소 항목의 시각은 그 값만 옮긴다.
UPDATED_AT_FIELD = "updatedAt"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ChatMemoryUnavailable(RuntimeError):
    """기억 API가 거절해 저장소 연산이 끝나지 못했음을 부른 쪽에 알린다."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"the memory API answered {status_code}")
        self.status_code = status_code


class ChatMemoryStore(BaseStore):
    """한 사용자의 chat 장기기억을 기억 API 뒤에 두고 읽고 쓰는 LangGraph 저장소다."""

    def __init__(self, client: ChatMemoryClient) -> None:
        self._client = client

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        """이 저장소는 비동기 그래프에서만 도므로 동기 배치는 지원하지 않는다."""
        raise NotImplementedError("ChatMemoryStore is async-only; use abatch")

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        """읽기·검색·적재를 기억 API 호출로 옮겨 부른 자리에서 끝낸다."""
        return [await self._run(op) for op in ops]

    async def _run(self, op: Op) -> Result:
        if isinstance(op, SearchOp):
            return await self._search(op)
        if isinstance(op, GetOp):
            return await self._get(op.key)
        if isinstance(op, PutOp):
            await self._put(op)
            return None
        if isinstance(op, ListNamespacesOp):
            return [MEMORY_NAMESPACE]
        raise NotImplementedError(f"chat memory does not support {type(op).__name__}")

    async def _search(self, op: SearchOp) -> list[SearchItem]:
        rows = await self._rows()
        window = rows[op.offset : op.offset + op.limit]
        return [
            SearchItem(
                namespace=MEMORY_NAMESPACE,
                key=str(row[KEY_FIELD]),
                value=_value(row),
                created_at=_at(row),
                updated_at=_at(row),
            )
            for row in window
        ]

    async def _get(self, key: str) -> Item | None:
        for row in await self._rows():
            if str(row[KEY_FIELD]) != key:
                continue
            return Item(
                namespace=MEMORY_NAMESPACE,
                key=key,
                value=_value(row),
                created_at=_at(row),
                updated_at=_at(row),
            )
        return None

    async def _put(self, op: PutOp) -> None:
        # 사용자에 대한 사실은 승인을 거쳐 적히므로 이 저장소는 되읽기만 한다.
        raise NotImplementedError("chat memory is written through the confirmation flow")

    async def _rows(self) -> list[dict[str, Any]]:
        payload = json.loads(_unwrapped(await self._client.recall()))
        rows = payload.get(FACTS_FIELD, []) if isinstance(payload, dict) else []
        return [row for row in rows if isinstance(row, dict) and KEY_FIELD in row]


def _unwrapped(result: ChatMemoryResult) -> str:
    if not result.ok:
        raise ChatMemoryUnavailable(result.status_code)
    return result.text


def _value(row: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in row.items() if name != KEY_FIELD}


def _at(row: dict[str, Any]) -> datetime:
    raw = row.get(UPDATED_AT_FIELD)
    if not isinstance(raw, str):
        return _EPOCH
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH
