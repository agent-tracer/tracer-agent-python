"""chat의 종결 노드가 이 턴의 마지막 어시스턴트 답변을 결과 계약으로 만든다."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from tracer_agent.shared.agents.chat.models import ChatResult, ChatState, SettleUpdate
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact_text

from ...runtime.llm.trajectory import step_content_text
from ...runtime.node import GraphNode


class SettleNode(GraphNode[ChatState, SettleUpdate]):
    """도구도 모델도 부르지 않고 이 턴의 산출을 결과 계약으로 좁힌다."""

    name = "settle"

    async def run(self, state: ChatState) -> SettleUpdate:
        result = ChatResult(
            assistantText=redact_text(final_text(state.get("messages") or []), stage=RedactionStage.OUTPUT),
            proposedWrites=state.get("proposals") or [],
        )
        return {"result": result}


def final_text(messages: list[Any]) -> str:
    """도구 호출로 끝난 메시지를 건너뛰고 사용자에게 보일 마지막 답변을 고른다."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            text = step_content_text(message.content)
            if text:
                return text
    return ""
