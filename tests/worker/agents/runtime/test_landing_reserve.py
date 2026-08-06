"""도구를 닫는 시점이 마무리 호출의 몫을 남기는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from tests.support.fakes import mk_rates
from tracer_agent.worker.agents.runtime.llm.budget import single_loop_budget
from tracer_agent.worker.agents.runtime.llm.pacing import landing_reserve_calls

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
