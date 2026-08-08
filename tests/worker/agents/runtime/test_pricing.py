from __future__ import annotations

from tracer_agent.shared.agents.shared.models import ModelRateDTO, UsageDTO
from tracer_agent.worker.agents.runtime.pricing import ModelRates

_RATES = ModelRates(
    {
        "claude-sonnet-4-6": ModelRateDTO(input=3.0, output=15.0, cacheWrite=3.75, cacheRead=0.3),
        "claude-haiku-4-5": ModelRateDTO(input=1.0, output=5.0, cacheWrite=1.25, cacheRead=0.1),
    }
)


def _usage() -> UsageDTO:
    return UsageDTO(
        inputTokens=1_000_000,
        outputTokens=1_000_000,
        cacheReadTokens=0,
        cacheCreationTokens=0,
    )


class TestEstimateCost:
    def test_봉투가_실어_온_요율로_계산한다(self) -> None:
        assert _RATES.estimate_cost_usd("claude-sonnet-4-6", _usage()) == 18.0

    def test_모델마다_다른_요율을_쓴다(self) -> None:
        assert _RATES.estimate_cost_usd("claude-haiku-4-5", _usage()) == 6.0

    def test_날짜가_붙은_구체_버전도_같은_요율이다(self) -> None:
        assert _RATES.estimate_cost_usd("claude-haiku-4-5-20251001", _usage()) == 6.0

    def test_봉투에_없는_모델은_None(self) -> None:
        assert _RATES.estimate_cost_usd("gpt-4o", _usage()) is None

    def test_usage가_없으면_None(self) -> None:
        assert _RATES.estimate_cost_usd("claude-sonnet-4-6", None) is None

    def test_캐시_토큰도_반영한다(self) -> None:
        usage = UsageDTO(
            inputTokens=0, outputTokens=0, cacheReadTokens=1_000_000, cacheCreationTokens=1_000_000
        )
        assert _RATES.estimate_cost_usd("claude-sonnet-4-6", usage) == 4.05
