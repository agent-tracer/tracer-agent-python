"""title-suggestion 그래프의 위상을 간선 스냅샷으로 고정한다."""

from __future__ import annotations

from tests.support.topology import edge_lines
from tracer_agent.worker.agents.title_suggestion.graph import TITLE_SUGGESTION_GRAPH


def test_그래프의_간선_집합을_고정한다() -> None:
    print("TITLE_SUGGESTION_GRAPH 간선 집합:")
    print(TITLE_SUGGESTION_GRAPH.get_graph().draw_ascii())
    assert edge_lines(TITLE_SUGGESTION_GRAPH) == {
        "__start__ → investigate",
        "investigate → validate_candidate",
        "validate_candidate ⇢ repair",
        "validate_candidate ⇢ finalize",
        "validate_candidate ⇢ empty",
        "repair → validate_candidate",
        "finalize → __end__",
        "empty → __end__",
    }
