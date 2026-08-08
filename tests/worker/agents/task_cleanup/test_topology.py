"""task-cleanup 그래프의 위상과 팬아웃 배선(Send 대상·비용 배분)을 고정한다."""

from __future__ import annotations

from tests.support.topology import edge_lines
from tracer_agent.shared.agents.task_cleanup.models import InspectDispatch, TriagePlan
from tracer_agent.worker.agents.task_cleanup.graph import TASK_CLEANUP_GRAPH, _dispatch


def test_그래프의_간선_집합을_고정한다() -> None:
    print("TASK_CLEANUP_GRAPH 간선 집합:")
    print(TASK_CLEANUP_GRAPH.compiled(None).get_graph().draw_ascii())
    assert edge_lines(TASK_CLEANUP_GRAPH.compiled(None)) == {
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


def test_후보_몫은_고른_depth에_비례해_턴과_달러를_함께_나눈다() -> None:
    plan = TriagePlan(
        inspect=[
            {"taskId": "task-1", "depth": "deep"},  # type: ignore[list-item]
            {"taskId": "task-2", "depth": "shallow"},  # type: ignore[list-item]
        ]
    )

    sends = _dispatch({"plan": plan, "max_cost_usd": 0.5, "max_turns": 5})  # type: ignore[typeddict-item]

    # Send는 페이로드를 직렬화하지 않고 계약 객체 그대로 노드에 넘긴다.
    assert all(isinstance(send.arg, InspectDispatch) for send in sends)
    budgets = {send.arg.assignment.taskId: send.arg.cost_budget for send in sends}
    turns = {send.arg.assignment.taskId: send.arg.max_turns for send in sends}
    # deep 4와 shallow 1의 몫에 전체 상한 $0.5를 곱해 0.4:0.1달러가 배분된다.
    assert budgets == {"task-1": 0.4, "task-2": 0.1}
    # 조율자가 고른 depth가 달러뿐 아니라 실제로 쓸 수 있는 턴에도 닿는다.
    assert turns == {"task-1": 4, "task-2": 1}


def test_턴이_남지_않으면_후보를_열지_않는다() -> None:
    # 달러가 남아도 턴 총량이 다한 실행은 조사를 더 열 수 없다.
    plan = TriagePlan(inspect=[{"taskId": "task-1", "depth": "deep"}])  # type: ignore[list-item]

    sends = _dispatch({"plan": plan, "max_cost_usd": 0.5, "max_turns": 0})  # type: ignore[typeddict-item]

    assert [send.node for send in sends] == ["empty"]


def test_열어볼_후보가_없으면_즉시_빈_결과로_끝낸다() -> None:
    # 조율자가 도구를 갖지 않으므로 조회할 후보가 없으면 단독으로 조사할 길이 없어 바로 끝난다.
    sends = _dispatch({"plan": TriagePlan(inspect=[]), "max_cost_usd": 2.0, "max_turns": 5})  # type: ignore[typeddict-item]

    assert [send.node for send in sends] == ["empty"]
