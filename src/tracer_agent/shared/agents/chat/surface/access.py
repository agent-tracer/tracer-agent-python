"""창구가 남의 대화를 존재조차 알리지 않도록 소유권을 한자리에서 검사한다."""

from __future__ import annotations

from ...runtime.ledger import SqlRow
from ..intake.turn import THREAD_NOT_FOUND, ChatIntakeRejected
from .ledger import ChatSurfaceLedger

EXECUTION_NOT_FOUND = (404, "not_found", "Chat execution not found")
CONFIRMATION_NOT_FOUND = (404, "not_found", "Confirmation not found")
CONFIRMATION_RESOLVED = (409, "conflict", "Confirmation already resolved")


async def owned_thread(ledger: ChatSurfaceLedger, user_id: str, thread_id: str) -> SqlRow:
    """이 사용자의 스레드 행을 읽고 아니면 없는 것으로 돌려보낸다."""
    thread = await ledger.find_thread(thread_id)
    if thread is None or thread["user_id"] != user_id:
        raise ChatIntakeRejected(*THREAD_NOT_FOUND)
    return thread


async def owned_execution(
    ledger: ChatSurfaceLedger, user_id: str, thread_id: str, execution_id: str
) -> SqlRow:
    """이 사용자의 이 스레드에 걸린 실행 행을 읽고 아니면 없는 것으로 돌려보낸다."""
    execution = await ledger.find_execution(execution_id)
    if execution is None or execution["user_id"] != user_id or execution["thread_id"] != thread_id:
        raise ChatIntakeRejected(*EXECUTION_NOT_FOUND)
    return execution
