"""title-suggestion의 사용자 범위 이벤트 조회와, 접수가 직접 받은 요청의 대화 컨텍스트 조립을 제공한다."""

from __future__ import annotations

from temporalio.exceptions import ApplicationError

from tracer_agent.shared.agents.shared.json_view import (
    JsonObject,
    as_int,
    as_object,
    as_objects,
    opt_text,
    text,
)
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext, TitleSuggestionTurn

from ..runtime.scoped_event_reader import timeline_path
from ..runtime.tracer_client import TracerApiPort
from .policy import windowed_turns

# TS 축의 TaskNotFoundError·TaskHasNoEventsError와 같은 뜻의 서브타입이며 재시도로 풀리지 않는다.
TASK_NOT_FOUND = "title.task-not-found"
TASK_HAS_NO_EVENTS = "title.task-has-no-events"

# 이벤트 건수만 필요하므로 한 장을 가장 좁게 읽고 전체 건수만 본다.
COUNT_PROBE_LIMIT = 1


async def load_title_context(tracer: TracerApiPort, task_id: str) -> TitleSuggestionContext:
    """SDK 축의 buildTitleContext와 같은 창 규칙으로 대화 컨텍스트를 추적 창구에서 조립한다."""
    detail = await tracer.get(f"/api/v1/tasks/{task_id}")
    if detail is None:
        raise ApplicationError(f"task not found: {task_id}", type=TASK_NOT_FOUND, non_retryable=True)
    task = as_object(as_object(detail)["task"])

    timeline = await tracer.get(timeline_path(task_id), {"limit": COUNT_PROBE_LIMIT})
    total_event_count = 0 if timeline is None else as_int(as_object(timeline).get("total"))
    if total_event_count == 0:
        raise ApplicationError(f"task has no events: {task_id}", type=TASK_HAS_NO_EVENTS, non_retryable=True)

    listed = await tracer.get(f"/api/v1/tasks/{task_id}/turns")
    rows = [] if listed is None else as_objects(as_object(listed).get("items"))
    turns = [_turn(row) for row in sorted(rows, key=lambda row: as_int(row.get("turnIndex")))]
    included, truncated = windowed_turns(turns)

    return TitleSuggestionContext(
        title=text(task["title"]),
        status=text(task["status"]),
        workspacePath=opt_text(task.get("workspacePath")),
        totalEventCount=total_event_count,
        totalTurnCount=len(turns),
        truncated=truncated,
        turns=included,
    )


def _turn(row: JsonObject) -> TitleSuggestionTurn:
    """대화 한 턴을 제목 조사가 읽는 모양으로 옮기며 빈 발화는 빈 문자열로 둔다."""
    return TitleSuggestionTurn(
        turnIndex=as_int(row.get("turnIndex")),
        askedText=opt_text(row.get("askedText")) or "",
        assistantText=opt_text(row.get("assistantText")),
    )
