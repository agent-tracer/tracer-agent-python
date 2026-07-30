"""title-suggestion의 사용자 범위 이벤트 조회와, 접수가 직접 받은 요청의 대화 컨텍스트 조립을 제공한다."""

from __future__ import annotations

from typing import Any

from temporalio.exceptions import ApplicationError

from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext, TitleSuggestionTurn

from ..runtime.scoped_event_reader import ScopedEventReader, timeline_path
from ..runtime.tracer_client import TracerApiClient
from .policy import windowed_turns

# 조회 로직이 task-cleanup과 완전히 같아 새 서브클래스 대신 이름만 이 슬라이스로 가져온다.
TitleLedgerReader = ScopedEventReader

# TS 축의 TaskNotFoundError·TaskHasNoEventsError와 같은 뜻의 서브타입이며 재시도로 풀리지 않는다.
TASK_NOT_FOUND = "title.task-not-found"
TASK_HAS_NO_EVENTS = "title.task-has-no-events"

# 이벤트 건수만 필요하므로 한 장을 가장 좁게 읽고 전체 건수만 본다.
COUNT_PROBE_LIMIT = 1


async def load_title_context(tracer: TracerApiClient, task_id: str) -> TitleSuggestionContext:
    """SDK 축의 buildTitleContext와 같은 창 규칙으로 대화 컨텍스트를 추적 창구에서 조립한다."""
    detail = await tracer.get(f"/api/v1/tasks/{task_id}")
    if detail is None:
        raise ApplicationError(f"task not found: {task_id}", type=TASK_NOT_FOUND, non_retryable=True)
    task: dict[str, Any] = detail["task"]

    timeline = await tracer.get(timeline_path(task_id), {"limit": COUNT_PROBE_LIMIT})
    total_event_count = int((timeline or {}).get("total") or 0)
    if total_event_count == 0:
        raise ApplicationError(f"task has no events: {task_id}", type=TASK_HAS_NO_EVENTS, non_retryable=True)

    listed = await tracer.get(f"/api/v1/tasks/{task_id}/turns")
    turns = [
        TitleSuggestionTurn(
            turnIndex=row["turnIndex"],
            askedText=row["askedText"] if row["askedText"] is not None else "",
            assistantText=row["assistantText"],
        )
        for row in sorted((listed or {}).get("items") or [], key=lambda row: row["turnIndex"])
    ]
    included, truncated = windowed_turns(turns)

    return TitleSuggestionContext(
        title=task["title"],
        status=task["status"],
        workspacePath=task.get("workspacePath"),
        totalEventCount=total_event_count,
        totalTurnCount=len(turns),
        truncated=truncated,
        turns=included,
    )
