"""도구를 닫는 시점이 마무리 호출의 몫을 남기고 그 여유가 공급자 백스톱 안에 머무는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from tests.support.contract import shared_contract
from tests.support.fakes import mk_rates
from tracer_agent.worker.agents.runtime.errors import BudgetExceeded
from tracer_agent.worker.agents.runtime.llm import pacing
from tracer_agent.worker.agents.runtime.llm.budget import single_loop_budget
from tracer_agent.worker.agents.runtime.llm.pacing import landing_reserve_calls, provider_budget_backstop

MODEL = "claude-sonnet-4-6"


def _call(output_tokens: int) -> AIMessage:
    """그 실행이 낸 모델 응답 하나이며 출력 토큰만 비용을 만든다."""
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 0,
            "output_tokens": output_tokens,
            "total_tokens": output_tokens,
        },
    )


class Test마무리몫예약:
    def test_계약이_마무리_호출_몫을_갖는다(self) -> None:
        assert landing_reserve_calls() >= 2

    def test_마무리_호출_몫이_모자라면_그_전에_도구를_닫는다(self) -> None:
        # 30_000 토큰은 0.45 달러라 예약 없이는 상한 1.0 에 닿지 않지만 마무리 몫까지 세면 닿는다.
        budget = single_loop_budget("title-suggestion", MODEL, 1.0, mk_rates())
        budget.charge(_call(30_000))

        assert budget.landing is True

    def test_아직_여유가_있으면_도구를_닫지_않는다(self) -> None:
        budget = single_loop_budget("title-suggestion", MODEL, 1.0, mk_rates())
        budget.charge(_call(1_000))

        assert budget.landing is False


class Test공급자백스톱:
    """공급자에게 넘기는 달러 상한은 실행을 조이는 자리보다 위에 있고 폭주만 끊는다."""

    def test_계약이_적은_배수를_실행_상한에_곱한다(self) -> None:
        declared = shared_contract("execution.budget.json")["pacing"]["landingReserve"]

        assert provider_budget_backstop(1.5) == pytest.approx(1.5 * declared["providerBackstop"])
        assert provider_budget_backstop(1.5) > 1.5

    def test_백스톱의_근거는_providerBackstop_하나다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 배수를 1 로 낮추면 마무리 몫으로 열리던 여유가 사라져 종료한 루프도 상한에서 끊긴다.
        declared = shared_contract("execution.budget.json")["pacing"]
        reserve = {**declared["landingReserve"], "providerBackstop": 1}
        monkeypatch.setattr(pacing, "_pacing", lambda: {**declared, "landingReserve": reserve})

        budget = single_loop_budget("title-suggestion", MODEL, 1.0, mk_rates())
        budget.charge(_call(40_000))
        budget.land()

        with pytest.raises(BudgetExceeded):
            budget.charge(_call(40_000))
