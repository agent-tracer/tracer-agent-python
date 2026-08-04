"""도구가 호출마다 실려 받는 실행 컨텍스트를 그래프 없이 세운다."""

from __future__ import annotations

from tests.support.fakes import mk_rates
from tracer_agent.shared.agents.recipe_scan.models import ProvenanceCatalog
from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch, CleanupCandidate
from tracer_agent.worker.agents.recipe_scan.reader import RecipeLedgerReader
from tracer_agent.worker.agents.recipe_scan.search import RecipeSearchReader
from tracer_agent.worker.agents.recipe_scan.tools import RecipeToolContext
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.budget import SharedToolLoopBudget, single_loop_budget
from tracer_agent.worker.agents.task_cleanup.reader import CleanupLedgerReader
from tracer_agent.worker.agents.task_cleanup.tools import CleanupToolContext
from tracer_agent.worker.agents.title_suggestion.reader import TitleLedgerReader
from tracer_agent.worker.agents.title_suggestion.tools import TitleToolContext

_MODEL = "claude-sonnet-4-6"


def _budget(agent_name: str) -> SharedToolLoopBudget:
    return single_loop_budget(agent_name, _MODEL, 2.0, mk_rates(), 0.0)


def mk_recipe_context(
    catalog: ProvenanceCatalog | None = None,
    *,
    tracer: FakeTracerApi | None = None,
    agent_name: str = "recipe-scan",
    max_model_turns: int = 5,
) -> RecipeToolContext:
    """recipe-scan 도구가 받는 조회 진입점과 근거 장부를 실은 컨텍스트를 만든다."""
    api = tracer or FakeTracerApi()
    return RecipeToolContext(
        agent_name=agent_name,
        trace=ExecutionTrace(),
        budget=_budget(agent_name),
        max_model_turns=max_model_turns,
        tool_owner=agent_name,
        reader=RecipeLedgerReader(api),  # type: ignore[arg-type]
        search=RecipeSearchReader(api),  # type: ignore[arg-type]
        catalog=catalog or ProvenanceCatalog(),
    )


def mk_cleanup_context(
    *,
    batch: CleanupBatch | None = None,
    tracer: FakeTracerApi | None = None,
    exposed_candidates: dict[str, CleanupCandidate] | None = None,
    event_ids_by_task: dict[str, set[str]] | None = None,
    agent_name: str = "task-cleanup",
    max_model_turns: int = 5,
) -> CleanupToolContext:
    """task-cleanup 도구가 받는 조회 진입점과 후보 배치와 근거 장부를 실은 컨텍스트를 만든다."""
    api = tracer or FakeTracerApi()
    return CleanupToolContext(
        agent_name=agent_name,
        trace=ExecutionTrace(),
        budget=_budget(agent_name),
        max_model_turns=max_model_turns,
        tool_owner=agent_name,
        reader=CleanupLedgerReader(api),  # type: ignore[arg-type]
        batch=batch or CleanupBatch(candidates=[]),
        exposed_candidates={} if exposed_candidates is None else exposed_candidates,
        event_ids_by_task={} if event_ids_by_task is None else event_ids_by_task,
    )


def mk_title_context(
    *,
    tracer: FakeTracerApi | None = None,
    agent_name: str = "title-suggestion",
    max_model_turns: int = 5,
) -> TitleToolContext:
    """title-suggestion 도구가 받는 사용자 범위 조회 진입점을 실은 컨텍스트를 만든다."""
    api = tracer or FakeTracerApi()
    return TitleToolContext(
        agent_name=agent_name,
        trace=ExecutionTrace(),
        budget=_budget(agent_name),
        max_model_turns=max_model_turns,
        tool_owner=agent_name,
        reader=TitleLedgerReader(api),  # type: ignore[arg-type]
    )
