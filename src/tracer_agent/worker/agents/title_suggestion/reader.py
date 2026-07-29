"""title-suggestion의 사용자 범위 이벤트 조회와, 접수가 직접 받은 요청의 대화 컨텍스트 조립을 제공한다."""

from __future__ import annotations

from temporalio.exceptions import ApplicationError

from tracer_agent.shared.agents.runtime.ledger import LedgerPoolProvider
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext, TitleSuggestionTurn

from ..runtime.scoped_event_reader import ScopedEventReader
from .policy import windowed_turns

# 조회 로직이 task-cleanup과 완전히 같아 새 서브클래스 대신 이름만 이 슬라이스로 가져온다.
TitleLedgerReader = ScopedEventReader

# TS 축의 TaskNotFoundError·TaskHasNoEventsError와 같은 뜻의 서브타입이며 재시도로 풀리지 않는다.
TASK_NOT_FOUND = "title.task-not-found"
TASK_HAS_NO_EVENTS = "title.task-has-no-events"

_TASK = "SELECT title, status, workspace_path FROM agent_task_view WHERE id = $1 AND user_id = $2"
_EVENT_COUNT = "SELECT count(*) FROM agent_event_view WHERE task_id = $1 AND user_id = $2"
_TURNS = """
    SELECT turn_index, asked_text, assistant_text FROM agent_turn_view
    WHERE task_id = $1 AND user_id = $2 ORDER BY turn_index ASC
"""


async def load_title_context(
    ledger: LedgerPoolProvider, user_id: str, task_id: str
) -> TitleSuggestionContext:
    """SDK 축의 buildTitleContext와 같은 창 규칙으로 대화 컨텍스트를 원장 뷰에서 조립한다."""
    pool = await ledger.pool()
    async with pool.acquire() as connection:
        task = await connection.fetchrow(_TASK, task_id, user_id)
        if task is None:
            raise ApplicationError(f"task not found: {task_id}", type=TASK_NOT_FOUND, non_retryable=True)
        total_event_count = await connection.fetchval(_EVENT_COUNT, task_id, user_id)
        rows = await connection.fetch(_TURNS, task_id, user_id)

    if total_event_count == 0:
        raise ApplicationError(f"task has no events: {task_id}", type=TASK_HAS_NO_EVENTS, non_retryable=True)

    turns = [
        TitleSuggestionTurn(
            turnIndex=row["turn_index"],
            askedText=row["asked_text"] if row["asked_text"] is not None else "",
            assistantText=row["assistant_text"],
        )
        for row in rows
    ]
    included, truncated = windowed_turns(turns)

    return TitleSuggestionContext(
        title=task["title"],
        status=task["status"],
        workspacePath=task["workspace_path"],
        totalEventCount=int(total_event_count),
        totalTurnCount=len(turns),
        truncated=truncated,
        turns=included,
    )
