"""검토 도구가 계약의 batchScope 대로 후보 배치 안만 읽는지 검증한다."""

from __future__ import annotations

from tests.support.tool_contexts import mk_cleanup_context
from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch, CleanupCandidate
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.task_cleanup.tools.get_events import (
    GetTaskEventsArgs,
    GetTaskEventsTool,
)

NOW = "2026-07-14T00:00:00.000Z"
ROWS = [{"id": "event-1", "seq": "1", "kind": "note", "title": "본문", "occurredAt": NOW}]


def _candidate(task_id: str) -> CleanupCandidate:
    return CleanupCandidate(
        id=task_id,
        visibleTitle=task_id,
        status="completed",
        lastEventAt=NOW,
        hasEvents=True,
        activeChildCount=0,
        candidateReasons=[],
    )


def _context(event_ids: dict[str, set[str]] | None = None):
    """후보 하나만 담은 배치와 어떤 태스크에도 이벤트를 내주는 대역을 실은 도구 컨텍스트다."""
    return mk_cleanup_context(
        batch=CleanupBatch(candidates=[_candidate("task-1")]),
        tracer=FakeTracerApi(ROWS),
        event_ids_by_task={} if event_ids is None else event_ids,
    )


class Test배치_밖_태스크:
    async def test_사용자_범위_안이어도_읽지_않는다(self) -> None:
        context = _context()

        answer = await GetTaskEventsTool().execute(GetTaskEventsArgs(taskId="outsider"), context)

        assert answer == "Task outsider not found."

    async def test_읽지_않았으므로_근거_장부에도_남기지_않는다(self) -> None:
        ledger: dict[str, set[str]] = {}
        context = _context(ledger)

        answer = await GetTaskEventsTool().execute(GetTaskEventsArgs(taskId="outsider"), context)
        GetTaskEventsTool().record(GetTaskEventsArgs(taskId="outsider"), answer, context)

        assert ledger == {}


class Test배치_안_태스크:
    async def test_그대로_읽는다(self) -> None:
        context = _context()

        answer = await GetTaskEventsTool().execute(GetTaskEventsArgs(taskId="task-1"), context)

        assert "event-1" in answer
