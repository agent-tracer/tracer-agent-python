"""캐시 경계가 턴마다 같은 앞부분에만 놓이는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from tracer_agent.worker.agents.runtime.llm.prompt_cache import PromptCacheMiddleware, volatile

_SUCCESS = ModelResponse(result=[])


class _Args(BaseModel):
    query: str


def _tool(name: str) -> StructuredTool:
    async def run(**_kwargs: Any) -> str:
        return "{}"

    return StructuredTool(name=name, description=name, args_schema=_Args, coroutine=run)


def _anthropic() -> ChatAnthropic:
    return ChatAnthropic(model="claude-haiku-4-5", api_key="sk-test")  # type: ignore[call-arg]


async def _bounded(request: ModelRequest[Any]) -> ModelRequest[Any]:
    seen: list[ModelRequest[Any]] = []

    async def handler(passed: ModelRequest[Any]) -> ModelResponse[Any]:
        seen.append(passed)
        return _SUCCESS

    await PromptCacheMiddleware(ttl="1h").awrap_model_call(request, handler)
    return seen[0]


def _cache_control(content: Any) -> dict[str, str] | None:
    if not isinstance(content, list) or not content:
        return None
    for block in reversed(content):
        if isinstance(block, dict) and "cache_control" in block:
            control: dict[str, str] = block["cache_control"]
            return control
    return None


async def test_경계가_호출마다_다시_쓰이는_꼬리_앞에_선다() -> None:
    # 꼬리 위에 놓으면 다음 호출의 앞부분이 달라져 그 항목을 다시 읽지 못한다.
    request: ModelRequest[Any] = ModelRequest(
        model=_anthropic(),
        messages=[HumanMessage(content="무엇이 무너졌나"), volatile(HumanMessage(content="2/5 턴"))],
    )

    passed = await _bounded(request)

    assert _cache_control(passed.messages[0].content) == {"type": "ephemeral", "ttl": "1h"}
    assert _cache_control(passed.messages[1].content) is None


async def test_시스템_프롬프트와_도구_선언에_경계가_선다() -> None:
    request: ModelRequest[Any] = ModelRequest(
        model=_anthropic(),
        messages=[HumanMessage(content="질문")],
        system_message=SystemMessage(content="너는 조사자다"),
        tools=[_tool("search_tasks"), _tool("get_task")],
    )

    passed = await _bounded(request)

    assert _cache_control(passed.system_message.content) is not None
    # 도구 선언은 한 덩어리로 실리므로 마지막 하나의 경계가 그 전부를 덮는다.
    assert passed.tools[-1].extras["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in (passed.tools[0].extras or {})


async def test_추론_블록에는_경계를_놓지_않는다() -> None:
    # 추론 블록을 고치면 서명이 깨지므로 경계는 그 앞의 메시지로 물러난다.
    request: ModelRequest[Any] = ModelRequest(
        model=_anthropic(),
        messages=[
            HumanMessage(content="무엇이 무너졌나"),
            AIMessage(content=[{"type": "thinking", "thinking": "곱씹는다", "signature": "sig"}]),
        ],
    )

    passed = await _bounded(request)

    assert _cache_control(passed.messages[0].content) is not None
    assert _cache_control(passed.messages[1].content) is None


async def test_다른_공급자의_모델은_그대로_지나간다() -> None:
    # 경계의 모양이 공급자마다 다르므로 모르는 모델에는 손대지 않는다.
    original = HumanMessage(content="질문")
    request: ModelRequest[Any] = ModelRequest(
        model=GenericFakeChatModel(messages=iter([])), messages=[original]
    )

    passed = await _bounded(request)

    assert passed.messages[0].content == "질문"
