"""브라우저가 친 대화 턴 하나를 소유권과 멱등을 확인해 원장에 접수한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ...runtime.ledger import LedgerSql, SqlRow, UniqueViolation
from .dispatch import ExecutionDispatch
from .ids import generate_ulid
from .ledger import QUEUED, ChatIntakeLedger
from .models import PostMessagePayload

THREAD_NOT_FOUND = (404, "not_found", "Thread not found")
ACTIVE_TURN_CONFLICT = (
    409,
    "chat.execution-active-conflict",
    "Chat thread already has an active turn",
)
IDEMPOTENCY_CONFLICT = (
    409,
    "chat.execution-idempotency-conflict",
    "Client request id was already used with different chat input",
)


class ChatIntakeRejected(Exception):
    """접수를 받아들일 수 없어 계약이 정한 상태와 코드로 돌려보낸다."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AcceptedChatTurn:
    """접수가 남긴 사용자 메시지 행과 실행 행이다."""

    message: SqlRow
    execution: SqlRow


class ChatTurnIntake:
    """사용자 메시지와 대기 실행을 한 트랜잭션으로 세우고 경쟁에 진 접수는 우선한 행으로 병합한다."""

    def __init__(self, sql: LedgerSql, dispatch: ExecutionDispatch) -> None:
        self._sql = sql
        self._ledger = ChatIntakeLedger(sql)
        self._dispatch = dispatch

    async def enqueue(
        self, user_id: str, thread_id: str, payload: PostMessagePayload, now: datetime
    ) -> AcceptedChatTurn:
        """턴 하나를 접수하고 대기 실행이면 워크플로 신호까지 보낸 결과를 낸다."""
        input_hash = payload.input_hash()
        try:
            async with self._sql.transaction():
                accepted = await self._accept(user_id, thread_id, payload, input_hash, now)
        except UniqueViolation:
            accepted = await self._resolve_existing(user_id, thread_id, payload.clientRequestId, input_hash)
        if accepted.execution["status"] == QUEUED:
            await self._dispatch.start(accepted.execution["id"], accepted.execution["thread_id"])
        return accepted

    async def _accept(
        self,
        user_id: str,
        thread_id: str,
        payload: PostMessagePayload,
        input_hash: str,
        now: datetime,
    ) -> AcceptedChatTurn:
        owner = await self._ledger.thread_owner(thread_id)
        if owner is None or owner != user_id:
            raise ChatIntakeRejected(*THREAD_NOT_FOUND)

        existing = await self._ledger.find_by_idempotency(user_id, thread_id, payload.clientRequestId)
        if existing is not None:
            return await self._existing(existing, input_hash)

        if await self._ledger.has_active_turn(thread_id):
            raise ChatIntakeRejected(*ACTIVE_TURN_CONFLICT)

        message = await self._ledger.insert_user_message(generate_ulid(now), thread_id, payload.content, now)
        execution = await self._ledger.insert_queued_execution(
            generate_ulid(now),
            user_id,
            thread_id,
            str(message["id"]),
            payload.clientRequestId,
            input_hash,
            payload.model,
            payload.language,
            now,
        )
        return AcceptedChatTurn(message=message, execution=execution)

    async def _resolve_existing(
        self, user_id: str, thread_id: str, client_request_id: str, input_hash: str
    ) -> AcceptedChatTurn:
        existing = await self._ledger.find_by_idempotency(user_id, thread_id, client_request_id)
        if existing is None:
            raise ChatIntakeRejected(*IDEMPOTENCY_CONFLICT)
        return await self._existing(existing, input_hash)

    async def _existing(self, execution: SqlRow, input_hash: str) -> AcceptedChatTurn:
        if execution["input_hash"] != input_hash:
            raise ChatIntakeRejected(*IDEMPOTENCY_CONFLICT)
        message = await self._ledger.find_message(str(execution["user_message_id"]))
        if message is None:
            raise RuntimeError("접수된 실행이 인용하는 사용자 메시지가 없다")
        return AcceptedChatTurn(message=message, execution=execution)
