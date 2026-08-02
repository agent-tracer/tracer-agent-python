"""실행 코드가 사용하는 세 에이전트 그래프를 ASCII 박스나 Mermaid로 출력한다."""

from __future__ import annotations

import sys
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from tracer_agent.worker.agents.recipe_scan.graph import RECIPE_SCAN_GRAPH
from tracer_agent.worker.agents.task_cleanup.graph import TASK_CLEANUP_GRAPH
from tracer_agent.worker.agents.title_suggestion.graph import TITLE_SUGGESTION_GRAPH

# 위상은 실행 상태를 보존하는지와 무관하므로 휘발 판으로 그린다.
GRAPHS: dict[str, CompiledStateGraph[Any, Any, Any, Any]] = {
    "recipe-scan": RECIPE_SCAN_GRAPH.compiled(None),
    "task-cleanup": TASK_CLEANUP_GRAPH.compiled(None),
    "title-suggestion": TITLE_SUGGESTION_GRAPH.compiled(None),
}

if __name__ == "__main__":
    mermaid = "--mermaid" in sys.argv[1:]
    for name, graph in GRAPHS.items():
        print(f"## {name}")
        drawable = graph.get_graph()
        print(drawable.draw_mermaid() if mermaid else drawable.draw_ascii())
