"""조사 상태가 체크포인트를 왕복해도 근거 판정이 같은 결과를 내는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog, RecipeScanRequest
from tracer_agent.worker.agents.recipe_scan.graph import RECIPE_SCAN_GRAPH


def _catalog() -> ProvenanceCatalog:
    return ProvenanceCatalog(
        eventIdsByTask={"t1": {"e1", "e2"}},
        turnIdsByTask={"t1": {"turn-1"}},
        ruleIds={"r1"},
        recipeRevs={"rec-1": 3},
    )


@pytest.fixture
def saved_state() -> dict[str, Any]:
    return {
        "task_id": "t1",
        "language": "auto",
        "user_prompt": None,
        "messages": [AIMessage(content="조사 결과")],
        "plan": None,
        "redispatch": None,
        "redispatch_count": 0,
        "reports": [],
        "provenance": _catalog(),
        "model_cost_usd": 0.5,
        "max_cost_usd": 1.0,
        "max_turns": 8,
        "model_turns_used": 2,
        "candidates": [],
        "validation_errors": [],
        "repair_attempted": False,
        "result": None,
    }


class Test체크포인트왕복:
    async def test_근거_장부가_왕복해도_같은_집합을_낸다(self, saved_state: dict[str, Any]) -> None:
        saver = InMemorySaver()
        graph = RECIPE_SCAN_GRAPH.compiled(saver)
        config = {"configurable": {"thread_id": "job-1"}}

        await graph.aupdate_state(config, saved_state)
        restored = await graph.aget_state(config)

        catalog = restored.values["provenance"]
        assert catalog.eventIdsByTask["t1"] == {"e1", "e2"}
        assert catalog.turnIdsByTask["t1"] == {"turn-1"}
        assert catalog.ruleIds == {"r1"}
        assert catalog.recipeRevs == {"rec-1": 3}

    async def test_대화_이력과_예산_잔량이_왕복해도_같다(self, saved_state: dict[str, Any]) -> None:
        saver = InMemorySaver()
        graph = RECIPE_SCAN_GRAPH.compiled(saver)
        config = {"configurable": {"thread_id": "job-2"}}

        await graph.aupdate_state(config, saved_state)
        restored = await graph.aget_state(config)

        assert [message.content for message in restored.values["messages"]] == ["조사 결과"]
        assert restored.values["max_cost_usd"] == 1.0
        assert restored.values["max_turns"] == 8

    async def test_세이버가_없으면_같은_판을_다시_컴파일하지_않는다(self) -> None:
        assert RECIPE_SCAN_GRAPH.compiled(None) is RECIPE_SCAN_GRAPH.compiled(None)

    async def test_같은_세이버는_같은_판을_쓴다(self) -> None:
        saver = InMemorySaver()

        assert RECIPE_SCAN_GRAPH.compiled(saver) is RECIPE_SCAN_GRAPH.compiled(saver)


class Test재개열쇠:
    def test_잡_실행의_식별자를_재개_열쇠로_삼는다(self) -> None:
        req = RecipeScanRequest.model_validate(
            {
                "model": "claude-sonnet-4-6",
                "apiKey": "sk-test",
                "modelRates": WIRE_MODEL_RATES,
                "limits": WIRE_LIMITS,
                "taskId": "t1",
                "userId": "user-1",
                "executionId": "exec-1",
            }
        )

        # 잡 봉투는 jobId 를 싣지 않으므로 실행 식별자가 재개의 범위를 갖는다.
        assert req.jobId is None
        assert (req.executionId or req.jobId) == "exec-1"
