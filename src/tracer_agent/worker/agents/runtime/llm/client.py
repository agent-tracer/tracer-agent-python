"""Anthropic 채팅 클라이언트의 실행 제한을 구성한다."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic

from tracer_agent.shared.agents.shared.models import AgentExecutionEnvelope

from .envelope import model_envelope

DEFAULT_MAX_OUTPUT_TOKENS = 8192


@dataclass(frozen=True)
class ChatPair:
    """이 실행이 부를 모델과, 공급자 오류 한 번을 대신 받을 모델이다."""

    primary: ChatAnthropic
    fallback: ChatAnthropic | None


def make_chat_pair(req: AgentExecutionEnvelope, *, streaming: bool = False) -> ChatPair:
    """실행 봉투가 정한 모델과 한도로 이 실행이 쓸 클라이언트 짝을 만든다."""

    def chat(model: str) -> ChatAnthropic:
        return make_chat(
            model,
            req.apiKey,
            req.deadlineMs,
            feature_max_output_tokens=req.limits.maxOutputTokens,
            streaming=streaming,
        )

    fallback_model = req.effective_fallback_model()
    return ChatPair(chat(req.model), None if fallback_model is None else chat(fallback_model))


def make_chat(
    model: str,
    api_key: str,
    deadline_ms: int,
    feature_max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    *,
    streaming: bool = False,
) -> ChatAnthropic:
    """모델 봉투로 출력 한도와 추론 설정을 정한 재시도 없는 채팅 클라이언트를 만든다."""
    envelope = model_envelope(model)
    max_output_tokens = envelope.max_output_tokens or feature_max_output_tokens
    # streaming을 켜면 ainvoke가 내부적으로 토큰을 흘려 LangGraph messages 스트림이 토큰을 낸다.
    kwargs: dict[str, object] = {
        "model": model,
        "api_key": api_key,
        "max_retries": 0,
        "timeout": max(1.0, deadline_ms / 1000),
        "max_tokens": max_output_tokens,
        "streaming": streaming,
    }
    if envelope.effort is not None:
        kwargs["reasoning_effort"] = envelope.effort
    if envelope.thinking is not None:
        kwargs["thinking"] = {"type": envelope.thinking}
    return ChatAnthropic(**kwargs)  # type: ignore[arg-type]
