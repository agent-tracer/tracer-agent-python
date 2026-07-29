"""task-cleanup 그래프의 위상과 팬아웃 배선(Send 대상·비용 배분)을 고정한다."""

from __future__ import annotations

from tests.support.topology import edge_lines
from tracer_agent.shared.agents.task_cleanup.models import InspectDispatch, TriagePlan
from tracer_agent.worker.agents.task_cleanup.graph import TASK_CLEANUP_GRAPH, _dispatch


def test_그래프의_간선_집합을_고정한다() -> None:
    print(TASK_CLEANUP_GRAPH.get_graph().draw_ascii())
    assert edge_lines(TASK_CLEANUP_GRAPH) == {
        "__start__ → triage",
        "triage ⇢ inspect",
        "triage ⇢ empty",
        "inspect → investigate",
        "investigate ⇢ inspect",
        "investigate ⇢ validate_decisions",
        "validate_decisions ⇢ repair",
        "validate_decisions ⇢ finalize",
        "validate_decisions ⇢ empty",
        "repair → validate_decisions",
        "finalize → __end__",
        "empty → __end__",
    }


def test_후보_비용_몫은_배분한_weight에_비례한다() -> None:
    plan = TriagePlan(
        inspect=[
            {"taskId": "task-1", "weight": 3},  # type: ignore[list-item]
            {"taskId": "task-2", "weight": 1},  # type: ignore[list-item]
        ]
    )

    sends = _dispatch({"plan": plan, "max_cost_usd": 0.5})  # type: ignore[typeddict-item]

    # Send는 페이로드를 직렬화하지 않고 계약 객체 그대로 노드에 넘긴다.
    assert all(isinstance(send.arg, InspectDispatch) for send in sends)
    budgets = {send.arg.assignment.taskId: send.arg.cost_budget for send in sends}
    # weight 합 4 중 3:1로 나눈 몫에 전체 상한 $0.5를 곱해 0.375:0.125달러가 배분된다.
    assert budgets == {"task-1": 0.375, "task-2": 0.125}


def test_열어볼_후보가_없으면_즉시_빈_결과로_끝낸다() -> None:
    # 조율자가 도구를 갖지 않으므로 열어볼 후보가 없으면 혼자 조사할 길이 없어 바로 끝난다.
    sends = _dispatch({"plan": TriagePlan(inspect=[]), "max_cost_usd": 2.0})  # type: ignore[typeddict-item]

    assert [send.node for send in sends] == ["empty"]
