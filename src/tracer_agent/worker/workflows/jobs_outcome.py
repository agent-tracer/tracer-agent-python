"""잡 하나의 실행 결과를 원장이 받을 종료 상태와 산출과 사용량으로 옮긴다."""

from __future__ import annotations

from typing import Any

from ...shared.agents.shared.models import AgentResponse


def status_and_error(response: AgentResponse) -> tuple[str, str | None]:
    """응답의 오류 유무와 서브타입만으로 원장이 쓸 종료 상태를 가른다."""
    if response.error is None:
        return "completed", None
    if response.error.subtype == "cancelled":
        return "canceled", response.error.summary
    return "failed", response.error.summary


def job_usage(response: AgentResponse, cost_usd: float | None) -> dict[str, Any]:
    """이 잡이 태운 모델의 별칭과 토큰과 비용을 원장 한 칸에 담을 모양으로 낸다."""
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
