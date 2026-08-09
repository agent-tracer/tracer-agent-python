"""공급자 일시 오류를 같은 모델로 먼저 재시도하는 런타임 소유 미들웨어를 만든다."""

from __future__ import annotations

from anthropic import APIConnectionError, OverloadedError, RateLimitError
from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware

from ...shared.execution_reservation import execution_budget_contract

# BudgetExceeded·OutputTruncated·취소는 anthropic 예외 계층 밖이라 이미 이 목록에서 빠져 있다.
PROVIDER_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    OverloadedError,
    RateLimitError,
    APIConnectionError,
)

# 도구를 같은 인자로 다시 부르는 횟수이며 계약이 갖지 않는 층이라 이 축이 정한다.
MAX_RETRIES = 2


def transient_retry() -> dict[str, float | bool]:
    """공급자가 잠시 받지 못한 질의를 다시 부르는 규칙이며 계약이 갖는다."""
    declared = execution_budget_contract()["runnerRetry"]["transient"]
    return {
        "max_retries": int(declared["attempts"]),
        "initial_delay": float(declared["initialDelayMs"]) / 1000,
        "backoff_factor": float(declared["backoffFactor"]),
        "jitter": bool(declared["jitter"]),
    }


def model_retry_middleware() -> ModelRetryMiddleware:
    """공급자 일시 오류만 같은 모델로 재시도하고 소진되면 예외를 그대로 다시 던진다."""
    return ModelRetryMiddleware(
        retry_on=PROVIDER_TRANSIENT_ERRORS,
        on_failure="error",
        **transient_retry(),  # type: ignore[arg-type]
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
