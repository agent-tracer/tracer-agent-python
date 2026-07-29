"""recipe-scan 노드를 그래프 밖에서 직접 실행해 실패 강등을 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import (
    WIRE_LIMITS,
    WIRE_MODEL_RATES,
    FakeLedger,
    FakeSearch,
    FakeToolLoopChat,
    mk_rates,
)
from tracer_agent.shared.agents.recipe_scan.models import (
    ProbeAssignment,
    ProbeDispatch,
    ProvenanceCatalog,
    RecipeScanRequest,
)
from tracer_agent.worker.agents.recipe_scan.nodes.probe import ProbeNode
from tracer_agent.worker.agents.recipe_scan.nodes.result import EmptyNode, FinalizeNode
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import ExecutionBudget

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-recipe"}


def _request(**overrides: Any) -> RecipeScanRequest:
    values: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "t1",
        "language": "ko",
        "userId": "user-1",
        "completionCallback": _COMPLETION,
    }
    values.update(overrides)
    return RecipeScanRequest.model_validate(values)


async def test_전문가_실행_예외는_실패_보고로_강등된다() -> None:
    class BoomChat(FakeToolLoopChat):
        async def ainvoke(self, _messages: list[object]) -> object:
            raise RuntimeError("agent blew up")

    req = _request()
    node = ProbeNode(
        req,
        RecipeLedgerReader(FakeLedger(), "user-1"),  # type: ignore[arg-type]
        RecipeSearchReader(FakeSearch(), "user-1"),  # type: ignore[arg-type]
        ExecutionTrace(),
        BoomChat([]),
        None,
        ExecutionBudget(1.0, mk_rates()),
        agent_name="recipe-scan",
    )

    result = await node.run(
        ProbeDispatch(
            assignment=ProbeAssignment(probe="timeline", weight=2, question="무엇"),
            cost_budget=1.0,
        )
    )

    # 예외를 던진 전문가는 판정을 실패로 싣고 소진 표시를 올려 조율자가 알게 한다.
    report = result["reports"][0]
    assert report.probe == "timeline"
    assert report.exhausted is True
    assert report.verdict == "Investigation failed: agent blew up"
    assert report.excerpts == []
    # 실패해도 지출은 합산에 실린다.
    assert "model_cost_usd" in result


async def test_종단_노드가_이_실행의_근거_장부를_결과에_싣는다() -> None:
    catalog = ProvenanceCatalog(
        eventIdsByTask={"task-1": {"event-2", "event-1"}},
        turnIdsByTask={"task-1": {"turn-1"}},
        ruleIds={"rule-1"},
        recipeRevs={"recipe-1": 3},
    )
    state: Any = {"candidates": [], "provenance": catalog}

    finalized = await FinalizeNode().run(state)
    empty = await EmptyNode().run(state)

    wired = {
        "eventIdsByTask": {"task-1": ["event-1", "event-2"]},
        "turnIdsByTask": {"task-1": ["turn-1"]},
        "ruleIds": ["rule-1"],
        "recipeRevs": {"recipe-1": 3},
    }
    # 워커가 소유권 밖의 인용까지 같은 기준으로 거르려면 빈 결과에도 장부가 실려야 한다.
    assert finalized["result"] == {"recipes": [], "provenance": wired}
    assert empty["result"] == {"recipes": [], "provenance": wired}
