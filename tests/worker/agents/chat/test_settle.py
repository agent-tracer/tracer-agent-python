"""chat 종결 단계가 모델도 HTTP도 없이 결과 계약을 만드는지 검증한다."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tracer_agent.shared.agents.chat.models import ChatState, ProposedWrite
from tracer_agent.worker.agents.chat.steps.settle import SettleStep, final_text


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


class Test종결단계:
    async def test_마지막_어시스턴트_답변을_결과로_낸다(self) -> None:
        update = await SettleStep().run(_state([HumanMessage(content="질문"), AIMessage(content="답변")]))

        assert update["result"].assistantText == "답변"

    async def test_도구_호출로_끝난_메시지는_답변으로_고르지_않는다(self) -> None:
        messages = [
            AIMessage(content="정리했습니다"),
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "get_task", "args": {}}]),
        ]

        update = await SettleStep().run(_state(messages))

        assert update["result"].assistantText == "정리했습니다"

    async def test_답변이_없으면_빈_문자열을_낸다(self) -> None:
        update = await SettleStep().run(_state([HumanMessage(content="질문")]))

        assert update["result"].assistantText == ""

    async def test_확인_대기_행을_결과가_인용한다(self) -> None:
        proposal = ProposedWrite(confirmationId="cf-1", toolName="remember_fact", args={"key": "k"})

        update = await SettleStep().run(_state([AIMessage(content="답변")], [proposal]))

        assert update["result"].proposedWrites == [proposal]

    async def test_답변의_텍스트_블록만_이어_붙인다(self) -> None:
        content = [
            {"type": "text", "text": "앞"},
            {"type": "thinking", "thinking": "숨김"},
            {"type": "text", "text": "뒤"},
        ]

        update = await SettleStep().run(_state([AIMessage(content=content)]))

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

        update = await SettleStep().run(old)

        assert update["result"].assistantText == "답변"
        assert update["result"].proposedWrites == []


class Test답변고르기:
    def test_최종_답변은_도구_호출이_없는_마지막_어시스턴트_텍스트다(self) -> None:
        messages = [
            AIMessage(
                content="확인해볼게요",
                tool_calls=[{"id": "call-1", "name": "search_tasks", "args": {}, "type": "tool_call"}],
            ),
            ToolMessage(content="검색 결과", tool_call_id="call-1"),
            AIMessage(content="최종 답변"),
        ]

        assert final_text(messages) == "최종 답변"

    def test_도구를_더_부르려다_끊긴_턴은_마지막으로_쓴_본문을_낸다(self) -> None:
        # 상한이나 예산으로 루프가 끊기면 마무리한 답이 없으므로 그때까지 쓴 본문이라도 사용자에게 간다.
        messages = [
            AIMessage(
                content="지금까지 찾은 것은 배포 실패 셋입니다",
                tool_calls=[{"id": "call-1", "name": "search_tasks", "args": {}, "type": "tool_call"}],
            ),
            ToolMessage(content="검색 결과", tool_call_id="call-1"),
        ]

        assert final_text(messages) == "지금까지 찾은 것은 배포 실패 셋입니다"
