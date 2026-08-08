"""대화 표면이 내는 거절의 어휘와 그것을 실어 나르는 예외를 소유한다."""

from __future__ import annotations

from ..shared.wire import EXECUTION_NOT_FOUND as EXECUTION_NOT_FOUND

THREAD_NOT_FOUND = (404, "not_found", "Thread not found")


class ChatRejected(Exception):
    """대화 창구가 요청을 받아들일 수 없어 계약이 정한 상태와 코드로 돌려보낸다."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ChatLedgerInvariantError(RuntimeError):
    """원장이 스스로 지켜야 할 불변식을 어긴 상태로 읽혔다."""
