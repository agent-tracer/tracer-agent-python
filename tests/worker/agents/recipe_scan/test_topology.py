"""recipe-scan 그래프의 위상과 팬아웃 배선(Send 대상·비용 배분)을 고정한다."""

from __future__ import annotations

from tests.support.topology import edge_lines
from tracer_agent.shared.agents.recipe_scan.models import DispatchPlan, ProbeDispatch
from tracer_agent.worker.agents.recipe_scan.graph import RECIPE_SCAN_GRAPH, _dispatch


def test_그래프의_간선_집합을_고정한다() -> None:
    print("RECIPE_SCAN_GRAPH 간선 집합:")
    print(RECIPE_SCAN_GRAPH.compiled(None).get_graph().draw_ascii())
    assert edge_lines(RECIPE_SCAN_GRAPH.compiled(None)) == {
        "__start__ → survey",
        "survey ⇢ probe",
        "survey ⇢ empty",
        "probe → investigate",
        "investigate ⇢ probe",
        "investigate ⇢ validate_candidate",
        "validate_candidate ⇢ repair",
        "validate_candidate ⇢ finalize",
        "validate_candidate ⇢ empty",
        "repair → validate_candidate",
        "finalize → __end__",
        "empty → __end__",
    }


def test_전문가_턴과_비용_몫은_배분한_weight에_비례한다() -> None:
    plan = DispatchPlan(
        probes=[
            {"probe": "timeline", "weight": 6, "question": "무엇을 했나"},  # type: ignore[list-item]
            {"probe": "rules", "weight": 2, "question": "어떤 규칙이"},  # type: ignore[list-item]
        ]
    )

    sends = _dispatch({"plan": plan, "max_cost_usd": 2.0, "max_turns": 8})  # type: ignore[typeddict-item]

    # Send는 페이로드를 직렬화하지 않고 계약 객체 그대로 노드에 넘긴다.
    assert all(isinstance(send.arg, ProbeDispatch) for send in sends)
    turns = {send.arg.assignment.probe: send.arg.max_turns for send in sends}
    budgets = {send.arg.assignment.probe: send.arg.max_cost_usd for send in sends}
    # weight 합 8 중 6:2로 나눈 몫에 가용 턴 8과 상한 $2.0을 곱해 6:2턴, 1.5:0.5달러가 배분된다.
    assert turns == {"timeline": 6, "rules": 2}
    assert budgets == {"timeline": 1.5, "rules": 0.5}


def test_띄울_전문가가_없으면_즉시_빈_결과로_끝낸다() -> None:
    # 조율자가 근거를 캐는 도구를 갖지 않아 혼자 조사할 길이 없으므로 계획이 비면 그대로 끝난다.
    empty = _dispatch({"plan": DispatchPlan(), "max_cost_usd": 2.0, "max_turns": 8})  # type: ignore[typeddict-item]
    missing = _dispatch({"plan": None, "max_cost_usd": 2.0, "max_turns": 8})  # type: ignore[typeddict-item]

    assert [send.node for send in empty] == ["empty"]
    assert [send.node for send in missing] == ["empty"]


def test_남은_예산이_없으면_전문가를_띄우지_않고_끝낸다() -> None:
    plan = DispatchPlan(probes=[{"probe": "timeline", "weight": 1, "question": "무엇을 했나"}])  # type: ignore[list-item]

    sends = _dispatch(
        {"plan": plan, "max_cost_usd": 2.0, "model_cost_usd": 2.0, "max_turns": 8}  # type: ignore[typeddict-item]
    )

    assert [send.node for send in sends] == ["empty"]


def test_남은_턴이_없으면_전문가를_띄우지_않고_끝낸다() -> None:
    plan = DispatchPlan(probes=[{"probe": "timeline", "weight": 1, "question": "무엇을 했나"}])  # type: ignore[list-item]

    sends = _dispatch(
        {"plan": plan, "max_cost_usd": 2.0, "max_turns": 8, "model_turns_used": 8}  # type: ignore[typeddict-item]
    )

    assert [send.node for send in sends] == ["empty"]
