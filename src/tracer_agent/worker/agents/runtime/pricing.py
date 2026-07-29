"""실행 봉투가 실어 온 단가로 지출 서킷브레이커의 비용을 환산한다."""

from __future__ import annotations

from collections.abc import Mapping

from tracer_agent.shared.agents.shared.models import ModelRateDTO, UsageDTO


class ModelRates:
    """한 실행이 쓰는 모델별 백만 토큰당 달러 단가표다."""

    def __init__(self, rates: Mapping[str, ModelRateDTO]) -> None:
        self._rates = dict(rates)

    def rate_for(self, model: str) -> ModelRateDTO | None:
        """모델의 단가를 찾으며 카탈로그에 없는 모델이면 None이다."""
        exact = self._rates.get(model)
        if exact is not None:
            return exact
        # 프로바이더는 별칭으로 부른 요청에도 날짜가 붙은 구체 버전 이름으로 응답할 수 있다.
        prefixed = [name for name in self._rates if model.startswith(f"{name}-")]
        if not prefixed:
            return None
        return self._rates[max(prefixed, key=len)]

    # 이 값은 그래프 내부 예산 상한에만 쓰고 보고·저장용 costUsd는 호출한 워커가 자기 카탈로그로 환산한다.
    def estimate_cost_usd(self, model: str, usage: UsageDTO | None) -> float | None:
        """모델이나 사용량을 모르면 오도하지 않도록 None을 낸다."""
        rate = self.rate_for(model)
        if rate is None or usage is None:
            return None
        cost = (
            usage.inputTokens * rate.input
            + usage.outputTokens * rate.output
            + usage.cacheCreationTokens * rate.cacheWrite
            + usage.cacheReadTokens * rate.cacheRead
        ) / 1_000_000
        return round(cost, 6)
