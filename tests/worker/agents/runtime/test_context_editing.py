"""맥락 정리 미들웨어가 오래된 도구 결과만 비우는 것을 검증한다."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tracer_agent.worker.agents.runtime.llm.standard_agent import context_editing_middleware

_MODEL = GenericFakeChatModel(messages=iter([]))
_SYSTEM = SystemMessage(content="system")
_TOOLS = [{"type": "function", "function": {"name": "search", "parameters": {}}}]


def _tool_round(index: int) -> list[Any]:
    call_id = f"call-{index}"
    return [
        AIMessage(content="", tool_calls=[{"id": call_id, "name": "search", "args": {}}]),
        ToolMessage(content=f"result-{index}", tool_call_id=call_id, name="search"),
    ]


def _messages(rounds: int) -> list[Any]:
    messages: list[Any] = [HumanMessage(content="investigate")]
    for index in range(rounds):
        messages.extend(_tool_round(index))
    return messages


def _request(messages: list[Any]) -> ModelRequest[Any]:
    return ModelRequest(model=_MODEL, messages=messages, system_message=_SYSTEM, tools=_TOOLS)


def test_도구_결과가_상한을_넘으면_오래된_것이_비워지고_최근_것은_남는다() -> None:
    middleware = context_editing_middleware()
    middleware.edits[0].trigger = 1
    middleware.edits[0].keep = 2
    captured: dict[str, Any] = {}

    def handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        captured["messages"] = request.messages
        return ModelResponse(result=[])

    middleware.wrap_model_call(_request(_messages(4)), handler)

    tool_messages = [message for message in captured["messages"] if isinstance(message, ToolMessage)]
    assert [message.content for message in tool_messages[:2]] == ["[cleared]", "[cleared]"]
    assert [message.content for message in tool_messages[2:]] == ["result-2", "result-3"]


def test_정리_뒤에도_캐시_접두사인_시스템_프롬프트와_도구_선언은_바뀌지_않는다() -> None:
    middleware = context_editing_middleware()
    middleware.edits[0].trigger = 1
    middleware.edits[0].keep = 0
    captured: dict[str, Any] = {}

    def handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        captured["request"] = request
        return ModelResponse(result=[])

    middleware.wrap_model_call(_request(_messages(2)), handler)

    request = captured["request"]
    assert request.system_message is _SYSTEM
    assert request.tools is _TOOLS
