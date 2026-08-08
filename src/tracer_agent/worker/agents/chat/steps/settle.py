"""chat의 종결 단계가 이 턴의 마지막 어시스턴트 답변을 결과 계약으로 만든다."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage

from tracer_agent.shared.agents.chat.models import ChatResult, ChatState, SettleUpdate
from tracer_agent.shared.agents.shared.redaction import RedactionStage, redact_text

from ...runtime.llm.trajectory import step_content_text

SETTLE_STEP = "settle"


class SettleStep:
    """도구도 모델도 부르지 않고 이 턴의 산출을 결과 계약으로 좁힌다."""

    async def run(self, state: ChatState) -> SettleUpdate:
        result = ChatResult(
            assistantText=redact_text(final_text(state.get("messages") or []), stage=RedactionStage.OUTPUT),
            proposedWrites=state.get("proposals") or [],
        )
        return {"result": result}


def final_text(messages: list[BaseMessage]) -> str:
    """마무리한 답이 있으면 그것을, 도구를 더 부르려다 끊겼으면 마지막으로 쓴 본문을 고른다."""
    partial = ""
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        text = step_content_text(message.content)
        if not text:
            continue
        if not message.tool_calls:
            return text
        partial = partial or text
    return partial
