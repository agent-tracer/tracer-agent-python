"""네 에이전트 상태가 예산 스냅숏을 누적으로 합치는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from tracer_agent.shared.agents.chat.models import ChatState
from tracer_agent.shared.agents.recipe_scan.models import RecipeScanState
from tracer_agent.shared.agents.shared.graph_state import fresh_budget_snapshot
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupState
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionState

_AGENT_STATES = [
    pytest.param(ChatState, id="chat"),
    pytest.param(RecipeScanState, id="recipe_scan"),
    pytest.param(TaskCleanupState, id="task_cleanup"),
    pytest.param(TitleSuggestionState, id="title_suggestion"),
]


async def _spend(_state: Any) -> dict[str, Any]:
    return {"model_cost_usd": 0.25, "model_turns_used": 1}


def _two_call_graph(state_schema: type[Any]) -> Any:
    """한 실행에서 모델을 두 번 부르는 그래프를 세운다."""
    builder: StateGraph[Any, Any, Any, Any] = StateGraph(state_schema)
    builder.add_node("first", _spend)
    builder.add_node("second", _spend)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    return builder.compile()


@pytest.mark.parametrize("state_schema", _AGENT_STATES)
async def test_두_번_부른_모델의_지출이_마지막_값이_아니라_합계로_남는다(state_schema: type[Any]) -> None:
    final = await _two_call_graph(state_schema).ainvoke(dict(fresh_budget_snapshot()))

    assert final["model_cost_usd"] == pytest.approx(0.5)
    assert final["model_turns_used"] == 2


@pytest.mark.parametrize("state_schema", _AGENT_STATES)
def test_모든_에이전트_상태가_재개가_읽는_두_칸을_갖는다(state_schema: type[Any]) -> None:
    assert {"model_cost_usd", "model_turns_used"} <= set(state_schema.__annotations__)
