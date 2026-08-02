"""공급자 일시 오류를 같은 모델로 먼저 재시도하는 런타임 소유 미들웨어를 만든다."""

from __future__ import annotations

from anthropic import APIConnectionError, OverloadedError, RateLimitError
from langchain.agents.middleware import ModelRetryMiddleware

# BudgetExceeded·OutputTruncated·취소는 anthropic 예외 계층 밖이라 이미 이 목록에서 빠져 있다.
PROVIDER_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    OverloadedError,
    RateLimitError,
    APIConnectionError,
)


def model_retry_middleware(*, max_retries: int = 2) -> ModelRetryMiddleware:
    """공급자 일시 오류만 같은 모델로 재시도하고 소진되면 예외를 그대로 다시 던진다."""
    return ModelRetryMiddleware(
        max_retries=max_retries,
        retry_on=PROVIDER_TRANSIENT_ERRORS,
        on_failure="error",
        backoff_factor=2.0,
        initial_delay=0.5,
        jitter=False,
    )
