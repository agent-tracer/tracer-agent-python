"""턴 상한에 닿은 대화가 그때까지의 답변을 지키는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat, mk_ai
from tests.support.prompts import CHAT_PROMPT
from tracer_agent.shared.agents.chat.models import ChatRequest
from tracer_agent.worker.agents.chat import agent as chat_mod
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace


class _NeverStopsChat(FakeToolLoopChat):
    """상한에 닿을 때까지 도구만 계속 부르는 대역이다."""

    def __init__(self) -> None:
        super().__init__([])
        self.calls = 0

    async def ainvoke(self, _messages: list[Any]) -> Any:
        self.calls += 1
        return mk_ai(
            content=f"계속 진행합니다 {self.calls}",
            tool_calls=[{"name": "list_tags", "args": {}, "id": f"c{self.calls}", "type": "tool_call"}],
        )


def _request() -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "model": "claude-haiku-4-5",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": {**WIRE_LIMITS, "maxTurns": 3},
            "threadId": "thread-1",
            "executionId": "execution-1",
            "userId": "user-1",
            "language": "ko",
            "messages": [{"role": "user", "content": "전부 찾아줘"}],
        }
    )


async def test_턴_상한에_닿아도_그때까지의_답변을_잃지_않는다(monkeypatch: Any) -> None:
    chat = _NeverStopsChat()
    monkeypatch.setattr(chat_mod, "make_chat", lambda *_a, **_k: chat)

    # 예외로 끊으면 사용자는 아무 답도 못 받고 SDK 백엔드와 결과가 갈라진다.
    result = await chat_mod.run_chat(_request(), None, ExecutionTrace(), CHAT_PROMPT)  # type: ignore[arg-type]

    assert chat.calls > 1
    assert result["assistantText"] != ""
