"""대기 중인 도구 호출 하나를 사용자의 결정대로 해소하고 그 결과를 대화에 잇는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ...runtime.ledger import LedgerSql, SqlRow
from ..intake.cancel import UpdateSignal
from ..intake.dispatch import ExecutionDispatch
from ..intake.follow_up import follow_up_client_request_id, follow_up_input_hash
from ..intake.ids import generate_ulid
from ..intake.ledger import ChatIntakeLedger
from ..rejections import ChatRejected
from .access import CONFIRMATION_NOT_FOUND, CONFIRMATION_RESOLVED, owned_thread
from .ledger import APPROVED, REJECTED, ChatSurfaceLedger
from .tool_client import ChatToolExecutor
from .updates import ChatExecutionUpdates

type ToolDecision = Literal["approve", "reject"]


@dataclass(frozen=True)
class ResolvedConfirmation:
    """해소된 확인 하나이며 창구는 이것을 봉투에 담기만 한다."""

    confirmation_id: str
    tool_name: str
    status: str
    result: str
    execution: SqlRow | None


class ThreadUpdateAnnouncer:
    """확인 대기는 스레드 것이므로 지금 열려 있는 실행 채널에 실어 다른 연결이 그것을 본다."""

    def __init__(
        self,
        ledger: ChatSurfaceLedger,
        updates: UpdateSignal | None = None,
        watch: ChatExecutionUpdates | None = None,
    ) -> None:
        self._ledger = ledger
        self._updates = updates
        self._watch = watch

    async def announce(self, thread_id: str) -> None:
        """이 스레드에서 열려 있는 실행이 있을 때만 그 채널로 갱신 사실을 보낸다."""
        active = await self._ledger.latest_active_execution(thread_id)
        if active is None:
            return
        # 이 프로세스의 연결에는 브로커를 거치지 않고 곧바로 알린다.
        if self._watch is not None:
            self._watch.notify(active)
        if self._updates is not None:
            await self._updates.publish(active, {"executionId": active})


class ChatConfirmationResolution:
    """대기 행의 자리를 먼저 집고, 그 자리로만 도구를 불러 결과를 대화와 이어 말할 턴으로 잇는다."""

    def __init__(
        self,
        sql: LedgerSql,
        executor: ChatToolExecutor,
        dispatch: ExecutionDispatch,
        announcer: ThreadUpdateAnnouncer,
    ) -> None:
        self._sql = sql
        self._ledger = ChatSurfaceLedger(sql)
        self._intake = ChatIntakeLedger(sql)
        self._executor = executor
        self._dispatch = dispatch
        self._announcer = announcer

    async def resolve(
        self,
        user_id: str,
        thread_id: str,
        confirmation_id: str,
        decision: ToolDecision,
        now: datetime,
    ) -> ResolvedConfirmation:
        """대기 중인 도구 호출 하나를 해소하고 대화에 남은 결과와 이어 말할 실행을 낸다."""
        await owned_thread(self._ledger, user_id, thread_id)
        pending = await self._pending(thread_id, confirmation_id)
        tool_name = str(pending["tool_name"])
        status = REJECTED if decision == "reject" else APPROVED
        # 전이를 먼저 확정해야 같은 확인의 두 승인이 상류 쓰기를 나란히 내지 못한다.
        resolved = await self._ledger.resolve_pending_tool(confirmation_id, status, now)
        if resolved is None:
            raise ChatRejected(*CONFIRMATION_RESOLVED)
        content = await self._decide(user_id, tool_name, pending, decision, confirmation_id)
        async with self._sql.transaction():
            anchor = await self._ledger.insert_tool_message(
                generate_ulid(now), thread_id, content, confirmation_id, now
            )
            execution = (
                None
                if status == REJECTED
                else await self._follow_up(user_id, thread_id, confirmation_id, anchor, now)
            )
        if execution is not None:
            await self._dispatch.start(str(execution["id"]), thread_id)
        await self._announcer.announce(thread_id)
        return ResolvedConfirmation(
            confirmation_id=confirmation_id,
            tool_name=tool_name,
            status=str(resolved["status"]),
            result=content,
            execution=execution,
        )

    async def _decide(
        self,
        user_id: str,
        tool_name: str,
        pending: SqlRow,
        decision: ToolDecision,
        confirmation_id: str,
    ) -> str:
        if decision == "reject":
            return f"User rejected the proposed {tool_name}. It was not executed."
        try:
            return await self._executor.execute(user_id, tool_name, dict(pending["args"] or {}))
        except BaseException:
            # 상류가 받지 못한 승인은 자리를 놓아 사용자가 같은 확인을 다시 물을 수 있게 한다.
            await self._ledger.reopen_pending_tool(confirmation_id)
            raise

    async def _follow_up(
        self, user_id: str, thread_id: str, confirmation_id: str, anchor: str, now: datetime
    ) -> SqlRow | None:
        """실행한 결과를 모델이 읽고 이어 말하도록 그 결과를 앵커로 삼는 턴을 세운다."""
        # 아직 문맥을 읽지 않은 턴만 이 결과를 이력으로 집으므로 그때만 줄을 더 세우지 않는다.
        if await self._ledger.latest_queued_execution(thread_id) is not None:
            return None
        previous = await self._ledger.list_executions(thread_id)
        return await self._intake.insert_queued_execution(
            generate_ulid(now),
            user_id,
            thread_id,
            anchor,
            follow_up_client_request_id(confirmation_id),
            follow_up_input_hash(confirmation_id),
            previous[0]["model"] if previous else None,
            previous[0]["language"] if previous else None,
            now,
        )

    async def _pending(self, thread_id: str, confirmation_id: str) -> SqlRow:
        pending = await self._ledger.find_pending_tool(confirmation_id)
        # 남의 스레드에 걸린 확인은 존재 자체를 알리지 않는다.
        if pending is None or pending["thread_id"] != thread_id:
            raise ChatRejected(*CONFIRMATION_NOT_FOUND)
        if pending["status"] != "pending":
            raise ChatRejected(*CONFIRMATION_RESOLVED)
        return pending
