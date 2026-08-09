"""턴마다 같은 프롬프트 앞부분에 캐시 경계를 놓고 호출마다 바뀌는 꼬리는 경계 밖에 둔다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool

# 이 표시가 붙은 메시지는 호출마다 다시 쓰이므로 캐시 경계가 그 앞에서 멈춘다.
VOLATILE_KEY = "agent_tracer.volatile"

# 경계를 놓으면 서명이 깨지거나 공급자가 거절하는 블록 종류다.
_UNCACHEABLE_BLOCK_TYPES = frozenset(
    {"thinking", "redacted_thinking", "server_tool_use", "code_execution_tool_result"}
)

type _CacheControl = dict[str, str]
type _Block = str | dict[Any, Any]


def volatile[MessageT: BaseMessage](message: MessageT) -> MessageT:
    """호출마다 다시 쓰이는 메시지임을 표시해 캐시 경계가 그 앞에서 멈추게 한다."""
    marked = {**message.additional_kwargs, VOLATILE_KEY: True}
    return message.model_copy(update={"additional_kwargs": marked})


class PromptCacheMiddleware(AgentMiddleware[Any, Any, Any]):
    """시스템 프롬프트와 도구 선언과 안정된 메시지 앞부분에 캐시 경계를 놓는다."""

    def __init__(self, *, ttl: Literal["5m", "1h"] = "1h") -> None:
        """캐시 항목이 살아 있는 시간을 정한다."""
        super().__init__()
        self._cache_control: _CacheControl = {"type": "ephemeral", "ttl": ttl}

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """이 호출의 프롬프트에 캐시 경계를 놓고 모델을 부른다."""
        # 경계의 모양이 공급자마다 달라 이 미들웨어는 Anthropic 모델에만 손을 댄다.
        if not isinstance(request.model, ChatAnthropic):
            return await handler(request)
        return await handler(self._bounded(request))

    def _bounded(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        overrides: dict[str, Any] = {}
        system_message = _tagged_system(request.system_message, self._cache_control)
        if system_message is not None:
            overrides["system_message"] = system_message
        tools = _tagged_tools(request.tools, self._cache_control)
        if tools is not None:
            overrides["tools"] = tools
        messages = _tagged_messages(request.messages, self._cache_control)
        if messages is not None:
            overrides["messages"] = messages
        return request.override(**overrides) if overrides else request


def _tagged_system(system_message: Any, cache_control: _CacheControl) -> SystemMessage | None:
    """시스템 프롬프트의 마지막 블록에 경계를 놓은 사본을 낸다."""
    if system_message is None:
        return None
    content = _tagged_content(system_message.content, cache_control)
    if content is None:
        return None
    tagged: SystemMessage = system_message.model_copy(update={"content": content})
    return tagged


def _tagged_tools(tools: Sequence[Any] | None, cache_control: _CacheControl) -> list[Any] | None:
    """도구 선언은 한 덩어리로 실리므로 마지막 하나에만 경계를 놓은 사본을 낸다."""
    if not tools:
        return None
    last = tools[-1]
    if not isinstance(last, BaseTool):
        return None
    extras = {**(last.extras or {}), "cache_control": cache_control}
    return [*tools[:-1], last.model_copy(update={"extras": extras})]


def _tagged_messages(
    messages: Sequence[BaseMessage], cache_control: _CacheControl
) -> list[BaseMessage] | None:
    """호출마다 다시 쓰이는 꼬리를 건너뛰고 그 앞의 마지막 메시지에 경계를 놓은 사본을 낸다."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.additional_kwargs.get(VOLATILE_KEY):
            continue
        content = _tagged_content(message.content, cache_control)
        if content is None:
            continue
        bounded = message.model_copy(update={"content": content})
        return [*messages[:index], bounded, *messages[index + 1 :]]
    return None


def _tagged_content(content: Any, cache_control: _CacheControl) -> list[_Block] | None:
    """경계를 받을 수 있는 마지막 블록에 경계를 놓은 본문을 내고 그런 블록이 없으면 아무것도 내지 않는다."""
    if isinstance(content, str):
        if not content:
            return None
        return [{"type": "text", "text": content, "cache_control": cache_control}]
    if not isinstance(content, list):
        return None
    for index in range(len(content) - 1, -1, -1):
        block = content[index]
        if not isinstance(block, dict) or block.get("type") in _UNCACHEABLE_BLOCK_TYPES:
            continue
        bounded: _Block = {**block, "cache_control": cache_control}
        return [*content[:index], bounded, *content[index + 1 :]]
    return None
