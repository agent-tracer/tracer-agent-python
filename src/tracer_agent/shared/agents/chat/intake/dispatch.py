"""접수가 세운 대기 실행을 실제로 시작하거나 끊도록 워크플로에 보내는 신호 창구다."""

from __future__ import annotations

from typing import Protocol


class ExecutionDispatch(Protocol):
    """대기 실행 하나를 실행하고 진행 중인 실행 하나를 끊는 워크플로 신호 창구다."""

    async def start(self, execution_id: str, thread_id: str) -> None:
        """스레드마다 하나인 워크플로를 세우거나 이미 있으면 그것에 이 실행을 신호로 올린다."""
        ...

    async def cancel(self, execution_id: str) -> None:
        """실행 하나만 끊으며 같은 스레드의 남은 턴은 계속 실행된다."""
        ...


class UnwiredExecutionDispatch:
    """워크플로 배선이 서기 전까지 대기 실행을 시작하지 않고 접수만 성립시킨다."""

    async def start(self, execution_id: str, thread_id: str) -> None:
        """보낼 창구가 아직 없으므로 아무 곳에도 신호하지 않고 접수를 막지도 않는다."""

    async def cancel(self, execution_id: str) -> None:
        """보낼 창구가 아직 없으므로 원장에 적힌 취소만 남기고 아무 곳에도 신호하지 않는다."""
