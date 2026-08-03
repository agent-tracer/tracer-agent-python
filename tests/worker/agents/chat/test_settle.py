"""chat 종결 노드가 모델도 HTTP도 없이 결과 계약을 만드는지 검증한다."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from tracer_agent.shared.agents.chat.models import ChatState, ProposedWrite
from tracer_agent.worker.agents.chat.nodes.settle import SettleNode


def _state(messages: list[Any], proposals: list[ProposedWrite] | None = None) -> ChatState:
    return {
        "language": "auto",
        "summary": None,
        "facts": [],
        "history": [],
        "messages": messages,
        "model_cost_usd": 0.0,
        "proposals": proposals or [],
        "result": None,
    }


class Test종결노드:
    async def test_마지막_어시스턴트_답변을_결과로_낸다(self) -> None:
        update = await SettleNode().run(_state([HumanMessage(content="질문"), AIMessage(content="답변")]))

        assert update["result"].assistantText == "답변"

    async def test_도구_호출로_끝난_메시지는_답변으로_고르지_않는다(self) -> None:
        messages = [
            AIMessage(content="정리했습니다"),
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "get_task", "args": {}}]),
        ]

        update = await SettleNode().run(_state(messages))

        assert update["result"].assistantText == "정리했습니다"

    async def test_답변이_없으면_빈_문자열을_낸다(self) -> None:
        update = await SettleNode().run(_state([HumanMessage(content="질문")]))

        assert update["result"].assistantText == ""

    async def test_확인_대기_행을_결과가_인용한다(self) -> None:
        proposal = ProposedWrite(confirmationId="cf-1", toolName="remember_fact", args={"key": "k"})

        update = await SettleNode().run(_state([AIMessage(content="답변")], [proposal]))

        assert update["result"].proposedWrites == [proposal]

    async def test_답변의_텍스트_블록만_이어_붙인다(self) -> None:
        content = [
            {"type": "text", "text": "앞"},
            {"type": "thinking", "thinking": "숨김"},
            {"type": "text", "text": "뒤"},
        ]

        update = await SettleNode().run(_state([AIMessage(content=content)]))

        assert update["result"].assistantText == "앞뒤"


class Test판이바뀌기전의체크포인트:
    async def test_새_칸이_없는_상태에서도_결과를_낸다(self) -> None:
        old: Any = {
            "language": "auto",
            "summary": None,
            "facts": [],
            "messages": [AIMessage(content="답변")],
            "model_cost_usd": 0.0,
            "result": None,
        }

        update = await SettleNode().run(old)

        assert update["result"].assistantText == "답변"
        assert update["result"].proposedWrites == []
