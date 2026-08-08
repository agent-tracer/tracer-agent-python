"""recipe-scan 도구 하나의 목록과 그 목록으로 세운 레지스트리를 소유한다."""

from __future__ import annotations

from typing import Any

from tracer_agent.shared.agents.recipe_scan.models import ProbeName

from ...runtime.tooling import AgentTool, ToolRegistry
from .context import RecipeToolContext
from .find_similar_tasks import FIND_SIMILAR_TASKS, FindSimilarTasksTool
from .get_task_events import GET_TASK_EVENTS, GetTaskEventsTool
from .get_task_summary import GET_TASK_SUMMARY, GetTaskSummaryTool
from .list_rules import LIST_RULES, ListRulesTool
from .search_events import SEARCH_EVENTS, SearchEventsTool
from .search_recipes import SEARCH_RECIPES, SearchRecipesTool

RECIPE_TOOL_CLASSES: tuple[type[AgentTool[Any, RecipeToolContext]], ...] = (
    GetTaskSummaryTool,
    GetTaskEventsTool,
    ListRulesTool,
    SearchEventsTool,
    FindSimilarTasksTool,
    SearchRecipesTool,
)

# 전문가는 자기 근거 원천에 닿는 도구 이름만 가진다.
PROBE_TOOLS: dict[ProbeName, tuple[str, ...]] = {
    "timeline": (GET_TASK_SUMMARY, GET_TASK_EVENTS, SEARCH_EVENTS),
    "rules": (LIST_RULES, SEARCH_RECIPES),
    "repetition": (SEARCH_EVENTS, FIND_SIMILAR_TASKS),
}

# 조율자는 근거를 직접 수집하지 않고 요청이 실어 준 인용 가능한 식별자만 본다.
COORDINATOR_TOOLS: tuple[str, ...] = ()

# 계획이 규모를 모른 채 서지 않도록 조율자가 요약 하나를 가진다.
SURVEY_TOOLS: tuple[str, ...] = (GET_TASK_SUMMARY,)

# 도구가 요청별 조회와 장부를 호출 컨텍스트로 받으므로 레지스트리 하나가 모든 실행을 함께 쓴다.
RECIPE_TOOLS: ToolRegistry[RecipeToolContext] = ToolRegistry(tuple(cls() for cls in RECIPE_TOOL_CLASSES))
