"""chat의 문맥 적재 단계가 이 턴이 읽을 재생 이력과 요약과 사실을 되살린다."""

from __future__ import annotations

import httpx

from tracer_agent.shared.agents.chat.models import ChatRequest, ChatState, LoadContextUpdate

from ..context import ChatContextReader

LOAD_CONTEXT_STEP = "load_context"

# 재생 문맥은 다시 물어 얻을 수 있으므로 연결 계열 오류만 정해진 횟수까지 다시 건다.
CONTEXT_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    httpx.TransportError,
    ConnectionError,
    TimeoutError,
)

CONTEXT_ATTEMPTS = 3


class LoadContextStep:
    """워커가 실어 보낸 이력이 없으면 에이전트 창구에서 이 턴의 재생 문맥을 읽는다."""

    def __init__(self, req: ChatRequest, http_client: httpx.AsyncClient) -> None:
        self._req = req
        self._http_client = http_client

    async def run(self, state: ChatState) -> LoadContextUpdate:
        req = self._req
        if not req.agentApiBaseUrl or req.messages:
            return {"history": req.messages, "summary": state["summary"], "facts": state["facts"]}
        history, summary, facts = await ChatContextReader(
            self._http_client,
            req.agentApiBaseUrl,
            req.userId,
            req.threadId,
            req.executionId,
            req.scopeToken or None,
        ).load()
        return {"history": history, "summary": summary, "facts": facts}
