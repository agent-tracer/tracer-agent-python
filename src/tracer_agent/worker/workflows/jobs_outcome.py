"""잡 하나의 실행 결과를 원장이 받을 종료 상태와 산출과 사용량으로 옮긴다."""

from __future__ import annotations

from typing import Any

from ...shared.agents.shared.models import AgentErrorDTO, AgentResponse

ATTEMPT_FAILED = "failed"


def status_and_error(response: AgentResponse) -> tuple[str, str | None]:
    """응답의 오류 유무와 서브타입만으로 원장이 쓸 종료 상태를 구분한다."""
    if response.error is None:
        return "completed", None
    if response.error.subtype == "cancelled":
        return "canceled", response.error.summary
    return "failed", response.error.summary


def job_usage(response: AgentResponse, cost_usd: float | None, attempt: int = 1) -> dict[str, Any]:
    """성공한 잡은 호출한 모델의 별칭과 토큰과 비용을, 끝내지 못한 잡은 시도 이력을 원장 한 칸에 담는다."""
    if response.error is not None:
        return {"attempts": [attempt_record(response, response.error, cost_usd, attempt)]}
    usage = response.usage
    return {
        # 별칭은 단가표의 키이므로 이 칸과 costUsd가 한 기록 안에서 같은 모델을 가리킨다.
        "model": response.modelUsed,
        "durationMs": response.durationMs,
        "costUsd": cost_usd,
        "numTurns": response.numTurns,
        "inputTokens": None if usage is None else usage.inputTokens,
        "outputTokens": None if usage is None else usage.outputTokens,
        "cacheReadTokens": None if usage is None else usage.cacheReadTokens,
        "cacheCreationTokens": None if usage is None else usage.cacheCreationTokens,
    }


def attempt_record(
    response: AgentResponse, error: AgentErrorDTO, cost_usd: float | None, attempt: int
) -> dict[str, Any]:
    """소진된 시도 하나가 잡 상세에 남기는 종결 요약이다."""
    usage = response.usage
    return {
        "attempt": attempt,
        "status": ATTEMPT_FAILED,
        "subtype": error.subtype,
        # 별칭은 단가표의 키이므로 이 칸과 costUsd가 한 기록 안에서 같은 모델을 가리킨다.
        "model": response.modelUsed,
        "costUsd": cost_usd,
        "durationMs": response.durationMs,
        "usage": None if usage is None else usage.model_dump(mode="json"),
        "errorMessage": error.summary,
        "providerRequestId": response.providerRequestId,
    }
