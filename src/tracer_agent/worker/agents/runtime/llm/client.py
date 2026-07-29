"""Anthropic 채팅 클라이언트의 실행 제한을 구성한다."""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

DEFAULT_MAX_OUTPUT_TOKENS = 8192


def make_chat(
    model: str,
    api_key: str,
    deadline_ms: int,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    *,
    streaming: bool = False,
) -> ChatAnthropic:
    """재시도 없이 데드라인을 따르는 Anthropic 채팅 클라이언트를 만든다."""
    # streaming을 켜면 ainvoke가 내부적으로 토큰을 흘려 LangGraph messages 스트림이 토큰을 낸다.
    kwargs: dict[str, object] = {
        "model": model,
        "api_key": api_key,
        "max_retries": 0,
        "timeout": max(1.0, deadline_ms / 1000),
        "max_tokens": max_output_tokens,
        "streaming": streaming,
    }
    return ChatAnthropic(**kwargs)  # type: ignore[arg-type]
