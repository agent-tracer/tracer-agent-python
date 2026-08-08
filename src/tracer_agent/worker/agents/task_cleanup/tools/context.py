"""task-cleanup 도구가 호출마다 실려 받는 요청별 조회와 그 호출만의 근거 장부를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

from tracer_agent.shared.agents.task_cleanup.models import CleanupBatch, CleanupCandidate

from ...runtime.llm.standard_agent import StandardAgentContext
from ...runtime.scoped_event_reader import ScopedEventReader


@dataclass(kw_only=True)
class CleanupToolContext(StandardAgentContext):
    """한 모델 호출이 연 도구가 함께 보는 조회 진입점과 그 호출에만 속한 근거 장부다."""

    tool_owner: str
    reader: ScopedEventReader
    batch: CleanupBatch
    exposed_candidates: dict[str, CleanupCandidate]
    event_ids_by_task: dict[str, set[str]]
