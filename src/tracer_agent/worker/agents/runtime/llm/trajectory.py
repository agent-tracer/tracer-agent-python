"""모델 메시지를 사용량과 반환 가능한 실행 단계로 해석한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

from tracer_agent.shared.agents.shared.models import AgentStepDTO, AgentStepRole, AgentStepToolCall, UsageDTO

_CACHE_CREATION_SUBKEYS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
MAX_STEP_CONTENT_BYTES = 32_000
# 궤적 화면이 이 값을 평문으로 그대로 보여주므로 블록 배열에서 이 종류만 이어 붙인다.
_TEXT_BLOCK_TYPE = "text"
_ROLE_BY_MESSAGE_TYPE: dict[str, AgentStepRole] = {
    "system": "system",
    "human": "user",
    "ai": "assistant",
    "tool": "tool",
}


@dataclass(frozen=True)
class TokenUsage:
    """한 모델 응답에서 해석한 토큰 사용량이다."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int

    def to_dto(self) -> UsageDTO:
        """가격 계산에 사용할 계약 객체를 만든다."""
        return UsageDTO(
            inputTokens=self.input_tokens,
            outputTokens=self.output_tokens,
            cacheReadTokens=self.cache_read_tokens,
            cacheCreationTokens=self.cache_creation_tokens,
        )


def extract_token_usage(message: BaseMessage) -> TokenUsage | None:
    """AI 메시지의 공급자별 캐시 토큰 표현을 공통 사용량으로 바꾼다."""
    if not isinstance(message, AIMessage) or not message.usage_metadata:
        return None
    meta = message.usage_metadata
    details = cast("dict[str, int]", meta.get("input_token_details") or {})
    cache_read = details.get("cache_read", 0) or 0
    cache_creation = (details.get("cache_creation", 0) or 0) + sum(
        details.get(key, 0) or 0 for key in _CACHE_CREATION_SUBKEYS
    )
    input_tokens = max(0, meta.get("input_tokens", 0) - cache_read - cache_creation)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=meta.get("output_tokens", 0),
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
    )


def message_identity(message: BaseMessage) -> tuple[str | None, str | None]:
    """AI 메시지의 실제 모델과 공급자 요청 식별자를 반환한다."""
    if not isinstance(message, AIMessage):
        return None, None
    return (
        message.response_metadata.get("model"),
        message.response_metadata.get("id"),
    )


def message_step(message: BaseMessage, seq: int) -> AgentStepDTO:
    """모델 대화 메시지를 외부 응답의 실행 단계로 바꾼다."""
    content, truncated = cap_step_content(step_content_text(message.content))
    usage = extract_token_usage(message)
    tool_calls: list[AgentStepToolCall] = []
    if isinstance(message, AIMessage):
        tool_calls = [_to_step_tool_call(call) for call in message.tool_calls]
    return AgentStepDTO(
        seq=seq,
        role=_step_role(message),
        content=content,
        truncated=truncated,
        toolCalls=tool_calls,
        toolName=message.name if isinstance(message, ToolMessage) else None,
        toolCallId=message.tool_call_id if isinstance(message, ToolMessage) else None,
        inputTokens=usage.input_tokens if usage else None,
        outputTokens=usage.output_tokens if usage else None,
        cacheReadTokens=usage.cache_read_tokens if usage else None,
        cacheCreationTokens=usage.cache_creation_tokens if usage else None,
        stopReason=(message.response_metadata.get("stop_reason") if isinstance(message, AIMessage) else None),
    )


def step_content_text(content: object) -> str:
    """모델이 낸 텍스트 자체를 실행 단계의 본문으로 만들며 도구 호출 블록은 싣지 않는다."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != _TEXT_BLOCK_TYPE:
            continue
        text = block.get("text")
        if isinstance(text, str):
            texts.append(text)
    return "".join(texts)


def step_carries_content(step: AgentStepDTO) -> bool:
    """본문도 도구 호출도 없는 단계는 궤적에 자리를 차지하지 않는다."""
    return bool(step.content.strip()) or bool(step.toolCalls)


def cap_step_content(value: str) -> tuple[str, bool]:
    """실행 단계 콘텐츠를 UTF-8 바이트 상한에 맞춘다."""
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_STEP_CONTENT_BYTES:
        return value, False
    return encoded[:MAX_STEP_CONTENT_BYTES].decode("utf-8", errors="ignore"), True


def is_truncated(message: BaseMessage) -> bool:
    """모델 응답이 출력 토큰 상한에서 잘렸는지 판정한다."""
    if not isinstance(message, AIMessage):
        return False
    return message.response_metadata.get("stop_reason") == "max_tokens"


def _step_role(message: BaseMessage) -> AgentStepRole:
    role = _ROLE_BY_MESSAGE_TYPE.get(message.type)
    if role is None:
        raise ValueError(f"unsupported message type for step recording: {message.type}")
    return role


def _to_step_tool_call(call: ToolCall) -> AgentStepToolCall:
    return AgentStepToolCall(
        id=call.get("id") or "",
        name=call["name"],
        args=dict(call.get("args") or {}),
    )
