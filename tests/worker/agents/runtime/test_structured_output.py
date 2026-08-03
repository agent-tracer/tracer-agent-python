"""agent의 넓은 출력을 구조화 응답 계약으로 좁히는 자리를 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.fakes import mk_rates
from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import ToolLoopBudget
from tracer_agent.worker.agents.runtime.llm.standard_agent import StandardAgentContext
from tracer_agent.worker.agents.runtime.llm.structured_agent import (
    invoke_structured_agent,
    narrow_agent_output,
)


def _context() -> StandardAgentContext:
    return StandardAgentContext(
        agent_name="recipe-scan",
        trace=ExecutionTrace(),
        budget=ToolLoopBudget("recipe-scan", "claude-sonnet-4-6", 1.0, mk_rates()),
        max_model_turns=4,
    )


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


class Test호출별실행상태:
    async def test_열쇠를_받은_호출은_그_열쇠로_실행_상태를_분리한다(self) -> None:
        seen: list[Any] = []

        class _Agent:
            async def ainvoke(self, _input: Any, *, context: Any, config: Any) -> Any:
                seen.append(config)
                return {"messages": [], "structured_response": RecipeDraft()}

        await invoke_structured_agent(
            _Agent(),  # type: ignore[arg-type]
            messages=[],
            context=_context(),
            response_type=RecipeDraft,
            recursion_limit=10,
            missing_response="없다",
            call_id="job-1:probe:timeline",
        )

        assert seen[0]["configurable"]["thread_id"] == "job-1:probe:timeline"

    async def test_열쇠가_없는_호출은_실행_상태를_보존하지_않는다(self) -> None:
        seen: list[Any] = []

        class _Agent:
            async def ainvoke(self, _input: Any, *, context: Any, config: Any) -> Any:
                seen.append(config)
                return {"messages": [], "structured_response": RecipeDraft()}

        await invoke_structured_agent(
            _Agent(),  # type: ignore[arg-type]
            messages=[],
            context=_context(),
            response_type=RecipeDraft,
            recursion_limit=10,
            missing_response="없다",
        )

        assert "configurable" not in seen[0]
