"""chat 문맥을 서버 재생 API에서 읽는 진입점과 그 이력의 모델 메시지 재생을 검증한다."""

from __future__ import annotations

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tracer_agent.shared.agents.chat.models import ChatHistoryMessage
from tracer_agent.worker.agents.chat.context import ChatContextReader, replay_messages

_BASE_URL = "http://tracer-api.test"


def _assistant(content: str, *calls: tuple[str, str]) -> ChatHistoryMessage:
    return ChatHistoryMessage.model_validate(
        {
            "role": "assistant",
            "content": content,
            "toolCalls": [{"id": call_id, "name": name, "args": {}} for call_id, name in calls],
        }
    )


def _tool(content: str, call_id: str | None) -> ChatHistoryMessage:
    return ChatHistoryMessage.model_validate({"role": "tool", "content": content, "toolCallId": call_id})


def _user(content: str) -> ChatHistoryMessage:
    return ChatHistoryMessage.model_validate({"role": "user", "content": content})


def _reader(client: httpx.AsyncClient, scope_token: str | None = "scope-1") -> ChatContextReader:
    return ChatContextReader(client, _BASE_URL, "user-1", "thread-1", "execution-1", scope_token)


async def test_문맥을_실행_식별자_하나로_재생_API에서_읽는다() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "messages": [{"role": "user", "content": "이번 질문"}],
                    "summary": "지난 이야기",
                    "facts": [{"key": "tz", "content": "Asia/Seoul"}],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        messages, summary, facts = await _reader(client).load()

    assert seen[0].url.path == "/api/v1/chat/threads/thread-1/executions/execution-1/replay"
    assert seen[0].headers["authorization"] == "Bearer scope-1"
    assert seen[0].headers["x-monitor-user"] == "user-1"
    assert [message.content for message in messages] == ["이번 질문"]
    assert summary == "지난 이야기"
    assert [fact.key for fact in facts] == ["tz"]


async def test_이력을_못_읽으면_빈_이력으로_넘어가지_않고_끊는다() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"ok": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="404"):
            await _reader(client).load()


def test_서버가_짝지어_준_도구_결과는_구조화_블록으로_되살린다() -> None:
    replayed = replay_messages(
        [
            _assistant("아카이브를 제안했습니다", ("call-1", "archive_task")),
            _tool("승인되어 완료됨", "call-1"),
        ]
    )

    assert isinstance(replayed[0], AIMessage)
    assert replayed[0].tool_calls == [
        {"id": "call-1", "name": "archive_task", "args": {}, "type": "tool_call"}
    ]
    assert isinstance(replayed[1], ToolMessage)
    assert replayed[1].tool_call_id == "call-1"


def test_서버가_인용을_지운_도구_결과는_평문_문맥으로_남는다() -> None:
    replayed = replay_messages([_assistant("제안했습니다"), _tool("승인되어 완료됨", None)])

    assert isinstance(replayed[0], AIMessage)
    assert replayed[0].tool_calls == []
    assert isinstance(replayed[1], HumanMessage)
    assert "승인되어 완료됨" in str(replayed[1].content)


def test_사용자_메시지는_그대로_되살린다() -> None:
    replayed = replay_messages([_user("다음 질문")])

    assert isinstance(replayed[0], HumanMessage)
    assert replayed[0].content == "다음 질문"
