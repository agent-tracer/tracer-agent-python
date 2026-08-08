"""대기 중인 도구 호출 하나를 사용자의 결정대로 해소하고 그 결과를 대화에 잇는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from ...runtime.ledger import SqlRow, SqlSource
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
        source: SqlSource,
        updates: UpdateSignal | None = None,
        watch: ChatExecutionUpdates | None = None,
    ) -> None:
        self._source = source
        self._updates = updates
        self._watch = watch

    async def announce(self, thread_id: str) -> None:
        """이 스레드에서 열려 있는 실행이 있을 때만 그 채널로 갱신 사실을 보낸다."""
        async with self._source.connect() as sql:
            active = await ChatSurfaceLedger(sql).latest_active_execution(thread_id)
        if active is None:
            return
        # 이 프로세스의 연결에는 브로커를 거치지 않고 곧바로 알린다.
        if self._watch is not None:
            self._watch.notify(active)
        if self._updates is not None:
            await self._updates.publish(active, {"executionId": active})


@dataclass(frozen=True)
class ClaimedConfirmation:
    """상류를 부르기 전에 원장에서 확정한 자리이며 그 뒤 구간은 연결 없이 이것만 읽는다."""

    tool_name: str
    args: dict[str, Any]
    status: str


class ChatConfirmationResolution:
    """대기 행의 자리를 먼저 가져가고, 그 자리로만 도구를 불러 결과를 대화와 이어 말할 턴으로 잇는다."""

    def __init__(
        self,
        source: SqlSource,
        executor: ChatToolExecutor,
        dispatch: ExecutionDispatch,
        announcer: ThreadUpdateAnnouncer,
    ) -> None:
        self._source = source
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
        claimed = await self._claim(user_id, thread_id, confirmation_id, decision, now)
        content = await self._decide(user_id, claimed, decision, confirmation_id)
        execution = await self._record(user_id, thread_id, confirmation_id, content, decision, now)
        if execution is not None:
            await self._dispatch.start(str(execution["id"]), thread_id)
        await self._announcer.announce(thread_id)
        return ResolvedConfirmation(
            confirmation_id=confirmation_id,
            tool_name=claimed.tool_name,
            status=claimed.status,
            result=content,
            execution=execution,
        )

    async def _claim(
        self,
        user_id: str,
        thread_id: str,
        confirmation_id: str,
        decision: ToolDecision,
        now: datetime,
    ) -> ClaimedConfirmation:
        """소유권과 대기 상태를 확인하고 이 요청이 상류를 부를 자리를 확정한다."""
        status = REJECTED if decision == "reject" else APPROVED
        async with self._source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            await owned_thread(ledger, user_id, thread_id)
            pending = await self._pending(ledger, thread_id, confirmation_id)
            # 전이를 먼저 확정해야 같은 확인의 두 승인이 상류 쓰기를 나란히 내지 못한다.
            resolved = await ledger.resolve_pending_tool(confirmation_id, status, now)
        if resolved is None:
            raise ChatRejected(*CONFIRMATION_RESOLVED)
        return ClaimedConfirmation(
            tool_name=str(pending["tool_name"]),
            args=dict(pending["args"] or {}),
            status=str(resolved["status"]),
        )

    async def _decide(
        self,
        user_id: str,
        claimed: ClaimedConfirmation,
        decision: ToolDecision,
        confirmation_id: str,
    ) -> str:
        if decision == "reject":
            return f"User rejected the proposed {claimed.tool_name}. It was not executed."
        try:
            # 확인 도구 가운데 둘은 이 서비스로 되돌아오므로 상류를 부르는 동안 원장 연결을 반납한다.
            return await self._executor.execute(user_id, claimed.tool_name, claimed.args)
        except BaseException:
            # 상류가 받지 못한 승인은 자리를 놓아 사용자가 같은 확인을 다시 물을 수 있게 한다.
            async with self._source.connect() as sql:
                await ChatSurfaceLedger(sql).reopen_pending_tool(confirmation_id)
            raise

    async def _record(
        self,
        user_id: str,
        thread_id: str,
        confirmation_id: str,
        content: str,
        decision: ToolDecision,
        now: datetime,
    ) -> SqlRow | None:
        """도구 결과 줄과 이어 말할 턴이 함께 남거나 함께 사라지도록 한 트랜잭션으로 적는다."""
        async with self._source.connect() as sql:
            ledger = ChatSurfaceLedger(sql)
            async with sql.transaction():
                anchor = await ledger.insert_tool_message(
                    generate_ulid(now), thread_id, content, confirmation_id, now
                )
                if decision == "reject":
                    return None
                return await self._follow_up(
                    ledger, ChatIntakeLedger(sql), user_id, thread_id, confirmation_id, anchor, now
                )

    async def _follow_up(
        self,
        ledger: ChatSurfaceLedger,
        intake: ChatIntakeLedger,
        user_id: str,
        thread_id: str,
        confirmation_id: str,
        anchor: str,
        now: datetime,
    ) -> SqlRow | None:
        """실행한 결과를 모델이 읽고 이어 말하도록 그 결과를 앵커로 삼는 턴을 세운다."""
        # 아직 문맥을 읽지 않은 턴만 이 결과를 이력으로 가져가므로 그때만 줄을 더 세우지 않는다.
        if await ledger.latest_queued_execution(thread_id) is not None:
            return None
        previous = await ledger.list_executions(thread_id)
        return await intake.insert_queued_execution(
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

    async def _pending(self, ledger: ChatSurfaceLedger, thread_id: str, confirmation_id: str) -> SqlRow:
        pending = await ledger.find_pending_tool(confirmation_id)
        # 남의 스레드에 걸린 확인은 존재 자체를 알리지 않는다.
        if pending is None or pending["thread_id"] != thread_id:
            raise ChatRejected(*CONFIRMATION_NOT_FOUND)
        if pending["status"] != "pending":
            raise ChatRejected(*CONFIRMATION_RESOLVED)
        return pending
