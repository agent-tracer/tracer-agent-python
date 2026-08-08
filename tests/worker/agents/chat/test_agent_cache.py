"""턴마다 대화 agent를 다시 컴파일하지 않고 같은 조건의 판을 다시 쓰는지 검증한다(네트워크 없음)."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tests.support.prompts import CHAT_PROMPT
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.shared.agents.envelope.tools import chat_tool_descriptions
from tracer_agent.worker.agents.chat.agent_cache import CachedChatAgents
from tracer_agent.worker.agents.chat.prompts import build_system_prompt

_PROMPT = build_system_prompt(CHAT_PROMPT)


def _request(**overrides: Any) -> ChatRequest:
    values: dict[str, Any] = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "threadId": "thread-1",
        "executionId": "execution-1",
        "userId": "user-1",
        "language": "ko",
        "messages": [{"role": "user", "content": "무엇을 했지"}],
        "toolDescriptions": chat_tool_descriptions(),
    }
    values.update(overrides)
    return ChatRequest.model_validate(values)


def test_같은_조건의_다음_턴은_컴파일한_판을_그대로_다시_쓴다() -> None:
    agents = CachedChatAgents()

    first = agents.compiled(_request(), _PROMPT, None)
    second = agents.compiled(_request(executionId="execution-2"), _PROMPT, None)

    # 실행 식별자는 판이 아니라 호출 설정이 싣는 값이라 판을 가르지 않는다.
    assert first is second
    assert agents.size() == 1


def test_다른_자격이나_모델이나_도구_설명은_판을_나눈다() -> None:
    agents = CachedChatAgents()
    base = agents.compiled(_request(), _PROMPT, None)

    other_key = agents.compiled(_request(apiKey="sk-other"), _PROMPT, None)
    other_model = agents.compiled(_request(model="claude-sonnet-4-6"), _PROMPT, None)
    other_tools = agents.compiled(_request(toolDescriptions={"get_task": "다른 설명"}), _PROMPT, None)
    other_prompt = agents.compiled(_request(), f"{_PROMPT} 덧붙임", None)

    assert len({id(base), id(other_key), id(other_model), id(other_tools), id(other_prompt)}) == 5
    assert agents.size() == 5


def test_세이버가_다르면_다른_판을_쓴다() -> None:
    agents = CachedChatAgents()
    saver = InMemorySaver()

    volatile = agents.compiled(_request(), _PROMPT, None)
    durable = agents.compiled(_request(), _PROMPT, saver)

    assert volatile is not durable
    assert agents.compiled(_request(), _PROMPT, saver) is durable


def test_자리가_넘치면_오래된_판부터_버린다() -> None:
    agents = CachedChatAgents(capacity=2)

    first = agents.compiled(_request(apiKey="sk-1"), _PROMPT, None)
    agents.compiled(_request(apiKey="sk-2"), _PROMPT, None)
    agents.compiled(_request(apiKey="sk-3"), _PROMPT, None)

    assert agents.size() == 2
    assert agents.compiled(_request(apiKey="sk-1"), _PROMPT, None) is not first
