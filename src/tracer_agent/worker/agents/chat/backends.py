"""이 턴의 도구가 부를 네 협력자를 요청 하나에서 만들고, 배선이 없는 자리는 없다고 답하는 자리로 채운다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.shared.agents.shared.json_view import JsonObject

from .memory import ChatMemoryClient
from .reader import ChatReadClient, ChatReadResult
from .store import KEY_FIELD, MEMORY_NAMESPACE, RECALL_LIMIT, ChatMemoryStore, ChatMemoryUnavailable
from .writer import ChatProposalResult, ChatWriteClient

# 배선이 빠진 실행에서 그 도구가 모델에게 대는 사유이며 진입점마다 자기 문장을 든다.
READ_BACKEND_MISSING = "the read backend is not available in this execution"
WRITE_BACKEND_MISSING = "the confirmation backend is not available in this execution"
MEMORY_BACKEND_MISSING = "the memory backend is not available in this execution"


@dataclass(frozen=True)
class RecalledFacts:
    """장기기억 되읽기 한 번의 결과이며, 실패면 모델에게 댈 사유를 싣는다."""

    ok: bool
    facts: list[JsonObject]
    reason: str = ""


class ChatReadPort(Protocol):
    """도구 하나를 계약이 선언한 되읽기 자리로 부르는 창구다."""

    async def read(self, tool_name: str, args: JsonObject, /) -> ChatReadResult:
        """되읽기 도구 하나를 실행 범위 자격과 함께 부른다."""
        ...


class ChatWritePort(Protocol):
    """쓰기 도구 하나를 실행하지 않고 확인 대기 행으로 세우는 창구다."""

    async def propose(self, tool_name: str, args: JsonObject, /) -> ChatProposalResult:
        """쓰기 도구 호출 하나를 대기 행으로 세운다."""
        ...


class ChatMemoryPort(Protocol):
    """이 사용자의 장기기억을 사실 목록으로 되읽는 창구다."""

    async def recall(self) -> RecalledFacts:
        """이 사용자에 대해 남은 사실 전체를 되읽는다."""
        ...


class UnwiredReadClient:
    """되읽기 진입점을 받지 못한 실행에서 도구가 지어내지 않고 없다고 답하게 한다."""

    async def read(self, _tool_name: str, _args: JsonObject, /) -> ChatReadResult:
        """부를 곳이 없으므로 되읽지 못한 사유만 낸다."""
        return ChatReadResult(ok=False, status_code=0, text="", unavailable=READ_BACKEND_MISSING)


class UnwiredWriteClient:
    """확인 창구를 받지 못한 실행에서 쓰기 도구가 대기 행을 지어내지 않게 한다."""

    async def propose(self, _tool_name: str, _args: JsonObject, /) -> ChatProposalResult:
        """세울 곳이 없으므로 대기 행을 세우지 못한 사유만 낸다."""
        return ChatProposalResult(
            ok=False, status_code=0, text="", confirmation_id="", unavailable=WRITE_BACKEND_MISSING
        )


class UnwiredChatMemory:
    """기억 API를 받지 못한 실행에서 장기기억 도구가 되읽었다고 말하지 않게 한다."""

    async def recall(self) -> RecalledFacts:
        """읽을 곳이 없으므로 되읽지 못한 사유만 낸다."""
        return RecalledFacts(ok=False, facts=[], reason=MEMORY_BACKEND_MISSING)


class ChatMemoryFacts:
    """장기기억 저장소가 든 항목을 도구가 모델에게 낼 사실 목록으로 되읽는다."""

    def __init__(self, store: ChatMemoryStore) -> None:
        self._store = store

    async def recall(self) -> RecalledFacts:
        """이 사용자에 대해 남은 사실 전체를 되읽고 창구가 거절하면 그 사유를 낸다."""
        try:
            items = await self._store.asearch(MEMORY_NAMESPACE, limit=RECALL_LIMIT)
        except ChatMemoryUnavailable as failure:
            return RecalledFacts(ok=False, facts=[], reason=str(failure))
        return RecalledFacts(ok=True, facts=[{KEY_FIELD: item.key, **item.value} for item in items])


@dataclass(frozen=True)
class ChatTurnBackends:
    """이 턴의 도구가 함께 보는 네 협력자이며 배선이 없는 자리도 자기 자리를 갖는다."""

    read: ChatReadPort
    agent_read: ChatReadPort
    write: ChatWritePort
    memory: ChatMemoryPort

    @classmethod
    def of(cls, req: ChatRequest, http_client: httpx.AsyncClient) -> ChatTurnBackends:
        """실행 봉투가 실어 보낸 진입점만큼만 협력자를 세우고 나머지는 없는 자리로 채운다."""
        scope_token = req.scopeToken or None
        return cls(
            read=(
                ChatReadClient(http_client, req.readApiBaseUrl, req.userId, scope_token)
                if req.readApiBaseUrl
                else UnwiredReadClient()
            ),
            # 잡 창구처럼 원장이 에이전트 서비스에 있는 읽기 도구는 추적이 아니라 이 기점을 부른다.
            agent_read=(
                ChatReadClient(http_client, req.agentApiBaseUrl, req.userId, scope_token)
                if req.agentApiBaseUrl
                else UnwiredReadClient()
            ),
            write=(
                ChatWriteClient(http_client, req.agentApiBaseUrl, req.userId, req.threadId, scope_token)
                if req.agentApiBaseUrl
                else UnwiredWriteClient()
            ),
            memory=(
                ChatMemoryFacts(
                    ChatMemoryStore(
                        ChatMemoryClient(http_client, req.agentApiBaseUrl, req.userId, scope_token)
                    )
                )
                if req.agentApiBaseUrl
                else UnwiredChatMemory()
            ),
        )
