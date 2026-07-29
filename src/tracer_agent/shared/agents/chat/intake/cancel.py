"""이 표면이 접수한 턴 하나를 원장에서 닫고 그 워크플로까지 끊는다."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ...runtime.ledger import LedgerSql, SqlRow
from ..execution_ledger import ChatExecutionLedger
from .dispatch import ExecutionDispatch
from .ledger import ChatIntakeLedger
from .turn import THREAD_NOT_FOUND, ChatIntakeRejected

EXECUTION_NOT_FOUND = (404, "not_found", "Chat execution not found")


class UpdateSignal(Protocol):
    """실행이 바뀌었다는 사실만 다른 replica에 흘리는 창구다."""

    async def publish(self, key: str, payload: dict[str, str]) -> bool:
        """갱신 사실 하나를 흘리고 보냈는지 낸다."""
        ...


class ChatTurnCancellation:
    """도는 턴을 취소로 닫고, 닫혔을 때에만 워크플로와 다른 replica에 알린다."""

    def __init__(
        self,
        sql: LedgerSql,
        dispatch: ExecutionDispatch,
        updates: UpdateSignal | None = None,
    ) -> None:
        self._sql = sql
        self._executions = ChatExecutionLedger(sql)
        self._threads = ChatIntakeLedger(sql)
        self._dispatch = dispatch
        self._updates = updates

    async def cancel(self, user_id: str, thread_id: str, execution_id: str, now: datetime) -> SqlRow:
        """실행 하나를 취소로 닫고 취소가 반영된 행을 낸다."""
        await self._require_owner(user_id, thread_id, execution_id)
        if await self._executions.cancel_active(execution_id, now) and self._updates is not None:
            await self._updates.publish(execution_id, {"executionId": execution_id})
        await self._dispatch.cancel(execution_id)
        canceled = await self._executions.find_by_id(execution_id)
        if canceled is None:
            raise ChatIntakeRejected(*EXECUTION_NOT_FOUND)
        return canceled

    async def _require_owner(self, user_id: str, thread_id: str, execution_id: str) -> None:
        owner = await self._threads.thread_owner(thread_id)
        if owner is None or owner != user_id:
            raise ChatIntakeRejected(*THREAD_NOT_FOUND)
        execution = await self._executions.find_by_id(execution_id)
        if execution is None or execution["thread_id"] != thread_id or execution["user_id"] != user_id:
            raise ChatIntakeRejected(*EXECUTION_NOT_FOUND)
