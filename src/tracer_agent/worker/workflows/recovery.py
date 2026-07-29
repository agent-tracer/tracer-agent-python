"""주인을 잃은 실행을 대기 자리로 되돌리고 아직 끝나지 않은 턴을 워크플로에 다시 태운다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ...shared.agents.chat.execution_ledger import ChatExecutionLedger
from ...shared.agents.chat.intake.dispatch import ExecutionDispatch
from ...shared.agents.runtime.ledger import SqlSource
from ...shared.workflows.chat_spec import RUNNING_LEASE_S


@dataclass(frozen=True)
class RecoveredExecutions:
    """되돌린 running 개수와 다시 태운 실행 개수다."""

    recovered: int
    resumed: int


async def resume_active_executions(
    sql: SqlSource, dispatch: ExecutionDispatch, now: datetime | None = None
) -> RecoveredExecutions:
    """갱신이 끊긴 running을 되돌린 뒤 아직 끝나지 않은 실행을 모두 다시 신호한다."""
    moment = now or datetime.now(UTC)
    async with sql.connect() as connection:
        ledger = ChatExecutionLedger(connection)
        # 주인이 사라진 running은 부분 유일 색인으로 그 스레드의 다음 턴까지 막는다.
        recovered = await ledger.recover_stale_running(moment - timedelta(seconds=RUNNING_LEASE_S), moment)
        active = await ledger.list_active()
    for row in active:
        await dispatch.start(str(row["id"]), str(row["thread_id"]))
    return RecoveredExecutions(recovered=recovered, resumed=len(active))
