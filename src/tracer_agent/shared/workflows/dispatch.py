"""접수가 세운 대기 실행을 스레드 워크플로에 신호로 얹고 없으면 그 워크플로를 연다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from .chat_spec import (
    CHAT_ENQUEUE_SIGNAL,
    CHAT_TASK_QUEUE,
    CHAT_THREAD_WORKFLOW,
    ChatExecutionRequest,
    ChatThreadRequest,
    execution_workflow_id,
    thread_workflow_id,
)

ClientFactory = Callable[[], Awaitable[Client]]


class TemporalClientProvider:
    """첫 신호가 필요할 때에만 Temporal에 붙고 그 연결을 뒤이은 접수가 함께 쓴다."""

    def __init__(self, connect: ClientFactory) -> None:
        self._connect = connect
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> Client:
        """연결이 없으면 만들고, 만들다 실패하면 다음 접수가 다시 시도하게 둔다."""
        async with self._lock:
            if self._client is None:
                self._client = await self._connect()
            return self._client


class TemporalExecutionDispatch:
    """대기 실행 하나를 그 스레드의 워크플로에 얹어 접수와 실행의 수명을 가른다."""

    def __init__(self, provider: TemporalClientProvider) -> None:
        self._provider = provider

    async def start(self, execution_id: str, thread_id: str) -> None:
        """스레드마다 하나인 워크플로를 세우거나 이미 있으면 그것에 이 실행을 신호로 얹는다."""
        client = await self._provider.client()
        await client.start_workflow(
            CHAT_THREAD_WORKFLOW,
            # 신호가 실행을 나르므로 워크플로를 처음 여는 경우에도 대기 줄은 비운 채 시작한다.
            ChatThreadRequest(thread_id),
            id=thread_workflow_id(thread_id),
            task_queue=CHAT_TASK_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            # 앞선 스레드 워크플로가 이미 닫혔으면 같은 식별자로 다시 열려야 다음 턴이 돈다.
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            start_signal=CHAT_ENQUEUE_SIGNAL,
            start_signal_args=[ChatExecutionRequest(execution_id, thread_id)],
        )

    async def cancel(self, execution_id: str) -> None:
        """실행 하나만 취소하며 같은 스레드의 남은 턴은 계속 흐른다."""
        client = await self._provider.client()
        await client.get_workflow_handle(execution_workflow_id(execution_id)).cancel()
