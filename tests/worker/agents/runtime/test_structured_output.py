"""agent의 넓은 출력을 구조화 응답 계약으로 좁히는 자리를 검증한다(모델 호출 없음)."""

from __future__ import annotations

import pytest

from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft
from tracer_agent.worker.agents.runtime.llm.structured_agent import narrow_agent_output


class Test구조화출력좁힘:
    def test_객체가_아닌_출력을_건다(self) -> None:
        with pytest.raises(ValueError, match="non-object"):
            narrow_agent_output("텍스트", RecipeDraft, "없다")

    def test_요구한_응답이_없으면_그_사유로_건다(self) -> None:
        with pytest.raises(ValueError, match="없다"):
            narrow_agent_output({"messages": []}, RecipeDraft, "없다")

    def test_다른_타입의_응답을_건다(self) -> None:
        with pytest.raises(ValueError, match="없다"):
            narrow_agent_output({"messages": [], "structured_response": 42}, RecipeDraft, "없다")

    def test_메시지_이력이_없으면_건다(self) -> None:
        with pytest.raises(ValueError, match="message history"):
            narrow_agent_output({"structured_response": RecipeDraft()}, RecipeDraft, "없다")

    def test_요구한_응답과_이력을_그대로_낸다(self) -> None:
        draft = RecipeDraft()

        narrowed = narrow_agent_output({"messages": [], "structured_response": draft}, RecipeDraft, "없다")

        assert narrowed["structured_response"] is draft
        assert narrowed["messages"] == []
