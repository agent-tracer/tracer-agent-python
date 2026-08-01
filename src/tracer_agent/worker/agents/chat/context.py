"""에이전트 창구의 재생 API에서 대화 문맥을 되읽고 저장된 이력을 모델 메시지로 되살린다."""

from __future__ import annotations

import json
from urllib.parse import quote

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from tracer_agent.shared.agents.chat.models import ChatFact, ChatHistoryMessage, ChatReplay

from .reader import USER_HEADER, unwrap_envelope

# 이번 턴이 되돌려 줄 이력을 서버가 계산해 주는 자리이며, 창 자르기도 도구 호출 짝 맞추기도 서버가 한다.
REPLAY_PATH = "/api/agent/chat/threads/{threadId}/executions/{executionId}/replay"


class ChatContextReader:
    """한 실행의 대화 문맥만 되읽도록 생성 시점에 범위가 묶인 HTTP 조회 진입점이다."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        user_id: str,
        thread_id: str,
        execution_id: str,
        scope_token: str | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._user_id = user_id
        self._thread_id = thread_id
        self._execution_id = execution_id
        self._scope_token = scope_token

    async def load(self) -> tuple[list[ChatHistoryMessage], str | None, list[ChatFact]]:
        """이번 턴이 모델에게 되돌려 줄 이력과 요약과 기억을 서버가 계산한 그대로 읽는다."""
        response = await self._client.get(f"{self._base_url}{self._path()}", headers=self._headers())
        # 이력을 못 읽은 채로 모델을 부르면 지난 대화를 통째로 잊은 답이 나가므로 실행을 여기서 끊는다.
        if response.status_code >= 400:
            raise ValueError(f"chat replay API answered {response.status_code}")
        replay = ChatReplay.model_validate(json.loads(unwrap_envelope(response.text)))
        return replay.messages, replay.summary, replay.facts

    def _path(self) -> str:
        return REPLAY_PATH.format(
            threadId=quote(self._thread_id, safe=""),
            executionId=quote(self._execution_id, safe=""),
        )

    def _headers(self) -> dict[str, str]:
        headers = {USER_HEADER: self._user_id}
        # 토큰이 있으면 서버가 자기신고 헤더 대신 토큰이 담은 사용자를 믿는다.
        if self._scope_token is not None:
            headers["authorization"] = f"Bearer {self._scope_token}"
        return headers


def replay_messages(history: list[ChatHistoryMessage]) -> list[BaseMessage]:
    """서버가 짝을 맞춰 준 이력을 모델 메시지로 되살린다."""
    messages: list[BaseMessage] = []
    for message in history:
        if message.role == "user":
            messages.append(HumanMessage(content=message.content))
        elif message.role == "assistant":
            messages.append(AIMessage(content=message.content, tool_calls=_calls(message)))
        elif message.role == "tool":
            messages.append(_replay_tool(message))
    return messages


def _calls(message: ChatHistoryMessage) -> list[dict[str, object]]:
    return [
        {"id": call.id, "name": call.name, "args": call.args, "type": "tool_call"}
        for call in message.toolCalls
    ]


ORPHAN_TOOL_RESULT_OPEN = '<orphan_tool_result source="untrusted">'
ORPHAN_TOOL_RESULT_CLOSE = "</orphan_tool_result>"


def _replay_tool(message: ChatHistoryMessage) -> BaseMessage:
    if message.toolCallId is not None:
        return ToolMessage(content=message.content, tool_call_id=message.toolCallId)
    # 사용자 발화로 옮기면 도구가 낸 문장이 지시문의 신뢰를 얻으므로 근거 구역으로 표시한다.
    return HumanMessage(content=f"{ORPHAN_TOOL_RESULT_OPEN}\n{message.content}\n{ORPHAN_TOOL_RESULT_CLOSE}")
