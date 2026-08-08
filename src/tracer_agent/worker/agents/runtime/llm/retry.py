"""공급자 일시 오류를 같은 모델로 먼저 재시도하는 런타임 소유 미들웨어를 만든다."""

from __future__ import annotations

from anthropic import APIConnectionError, OverloadedError, RateLimitError
from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware

# BudgetExceeded·OutputTruncated·취소는 anthropic 예외 계층 밖이라 이미 이 목록에서 빠져 있다.
PROVIDER_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    OverloadedError,
    RateLimitError,
    APIConnectionError,
)

# 같은 자리를 다시 부르는 횟수이며 모델과 도구가 같은 값을 쓴다.
MAX_RETRIES = 2


def model_retry_middleware(*, max_retries: int = MAX_RETRIES) -> ModelRetryMiddleware:
    """공급자 일시 오류만 같은 모델로 재시도하고 소진되면 예외를 그대로 다시 던진다."""
    return ModelRetryMiddleware(
        max_retries=max_retries,
        retry_on=PROVIDER_TRANSIENT_ERRORS,
        on_failure="error",
        backoff_factor=2.0,
        initial_delay=0.5,
        jitter=True,
    )


def tool_retry_middleware(transient_errors: tuple[type[Exception], ...]) -> ToolRetryMiddleware:
    """도구가 일시 오류라고 선언한 것만 같은 인자로 다시 부르고 소진되면 그대로 던진다."""
    return ToolRetryMiddleware(
        max_retries=MAX_RETRIES,
        retry_on=transient_errors,
        on_failure="error",
        backoff_factor=2.0,
        initial_delay=0.5,
        jitter=True,
    )
