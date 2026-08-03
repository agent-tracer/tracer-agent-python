"""스레드의 턴을 하나씩 흘리고 실행 하나의 준비와 생성과 종결을 소유하는 워크플로 둘이다."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError

from ...shared.workflows.chat_spec import (
    CHAT_ENQUEUE_SIGNAL,
    CHAT_EXECUTION_WORKFLOW,
    CHAT_THREAD_WORKFLOW,
    FAIL_ACTIVITY,
    FAIL_TIMEOUT_S,
    FINALIZE_ACTIVITY,
    FINALIZE_TIMEOUT_S,
    GENERATE_ACTIVITY,
    GENERATE_HEARTBEAT_TIMEOUT_S,
    GENERATE_MAX_ATTEMPTS,
    GENERATE_TIMEOUT_S,
    NEXT_EXECUTION_ACTIVITY,
    NEXT_EXECUTION_TIMEOUT_S,
    PREPARE_ACTIVITY,
    PREPARE_TIMEOUT_S,
    STAGE_MAX_ATTEMPTS,
    THREAD_BUSY_FAILURE,
    THREAD_BUSY_MAX_ROUNDS,
    THREAD_BUSY_RETRY_S,
    THREAD_IDLE_S,
    THREAD_MAX_CHILDREN,
    ChatExecutionRequest,
    ChatThreadRequest,
    FailedChatExecution,
    GeneratedChatExecution,
    PreparedChatExecution,
    execution_workflow_id,
)


def _thread_busy(error: BaseException) -> bool:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, ApplicationError) and cause.type == THREAD_BUSY_FAILURE:
            return True
        cause = cause.__cause__
    return False


@workflow.defn(name=CHAT_EXECUTION_WORKFLOW)
class ChatExecutionWorkflow:
    """브라우저와 API 연결 밖에서 대화 실행 하나를 소유하고 실패 상태를 끝까지 기록한다."""

    @workflow.run
    async def run(self, request: ChatExecutionRequest) -> None:
        """준비와 생성과 종결을 차례로 돌리고 실패로 끝나면 그 사유를 원장에 남긴다."""
        try:
            prepared = await self._prepare_until_thread_frees(request)
            generated = await self._generate(prepared)
            await self._settle(generated)
        except Exception as error:
            await workflow.execute_activity(
                FAIL_ACTIVITY,
                FailedChatExecution(request.execution_id, str(error)),
                start_to_close_timeout=timedelta(seconds=FAIL_TIMEOUT_S),
                retry_policy=RetryPolicy(maximum_attempts=STAGE_MAX_ATTEMPTS),
            )
            raise

    async def _prepare_until_thread_frees(self, request: ChatExecutionRequest) -> PreparedChatExecution:
        for round_number in range(1, THREAD_BUSY_MAX_ROUNDS + 1):
            try:
                return await self._prepare(request)
            except Exception as error:
                # 잠긴 스레드는 이 실행의 결함이 아니므로 queued로 살려 둔 채 자리가 나기를 기다린다.
                if round_number >= THREAD_BUSY_MAX_ROUNDS or not _thread_busy(error):
                    raise
                await asyncio.sleep(THREAD_BUSY_RETRY_S)
        raise ApplicationError("chat thread never freed", non_retryable=True)

    async def _prepare(self, request: ChatExecutionRequest) -> PreparedChatExecution:
        prepared: PreparedChatExecution = await workflow.execute_activity(
            PREPARE_ACTIVITY,
            request,
            result_type=PreparedChatExecution,
            start_to_close_timeout=timedelta(seconds=PREPARE_TIMEOUT_S),
            retry_policy=RetryPolicy(maximum_attempts=STAGE_MAX_ATTEMPTS),
        )
        return prepared

    async def _generate(self, prepared: PreparedChatExecution) -> GeneratedChatExecution:
        generated: GeneratedChatExecution = await workflow.execute_activity(
            GENERATE_ACTIVITY,
            prepared,
            result_type=GeneratedChatExecution,
            start_to_close_timeout=timedelta(seconds=GENERATE_TIMEOUT_S),
            heartbeat_timeout=timedelta(seconds=GENERATE_HEARTBEAT_TIMEOUT_S),
            # 기본값 TRY_CANCEL은 취소 즉시 실패로 접어 그때까지 쓴 답변을 버리므로 최종 상태를 기다린다.
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            retry_policy=RetryPolicy(maximum_attempts=GENERATE_MAX_ATTEMPTS),
        )
        return generated

    async def _settle(self, generated: GeneratedChatExecution) -> None:
        finalize = asyncio.ensure_future(
            workflow.execute_activity(
                FINALIZE_ACTIVITY,
                generated,
                start_to_close_timeout=timedelta(seconds=FINALIZE_TIMEOUT_S),
                retry_policy=RetryPolicy(maximum_attempts=STAGE_MAX_ATTEMPTS),
            )
        )
        try:
            await asyncio.shield(finalize)
        except asyncio.CancelledError:
            # 취소가 이미 걸린 뒤에도 종결은 끝나야 사용자가 화면에서 본 답변이 원장에 남는다.
            await finalize
            raise


@workflow.defn(name=CHAT_THREAD_WORKFLOW)
class ChatThreadWorkflow:
    """스레드 안의 실행을 접수 순서대로 하나씩 기다려 모델 호출이 겹치지 않게 한다."""

    def __init__(self) -> None:
        self._wake = False
        self._completed = 0

    @workflow.signal(name=CHAT_ENQUEUE_SIGNAL)
    def enqueue(self, _execution_id: str) -> None:
        """대기 줄이 움직였다는 것만 받고 실행의 사실은 원장에서 다시 조회한다."""
        self._wake = True

    @workflow.run
    async def run(self, request: ChatThreadRequest) -> None:
        """원장의 대기 줄을 순서대로 비우고 유휴가 이어지면 스레드 워크플로를 닫는다."""
        while True:
            if not await self._wait_for_work():
                return
            self._wake = False
            await self._drain(request)

    async def _drain(self, request: ChatThreadRequest) -> None:
        last_failed: str | None = None
        while True:
            execution_id = await workflow.execute_activity(
                NEXT_EXECUTION_ACTIVITY,
                request.thread_id,
                start_to_close_timeout=timedelta(seconds=NEXT_EXECUTION_TIMEOUT_S),
                retry_policy=RetryPolicy(maximum_attempts=STAGE_MAX_ATTEMPTS),
            )
            if execution_id is None:
                return
            # 같은 실행이 연달아 실패하면 원장에 그대로 남아 무한히 다시 집히므로 이번 회차를 줄인다.
            if execution_id == last_failed:
                return
            if await self._run_child(request.thread_id, execution_id):
                last_failed = None
                self._completed += 1
                if self._completed >= THREAD_MAX_CHILDREN:
                    workflow.continue_as_new(ChatThreadRequest(request.thread_id))
            else:
                last_failed = execution_id

    async def _wait_for_work(self) -> bool:
        try:
            await workflow.wait_condition(lambda: self._wake, timeout=THREAD_IDLE_S)
        except TimeoutError:
            return False
        return True

    async def _run_child(self, thread_id: str, execution_id: str) -> bool:
        try:
            await workflow.execute_child_workflow(
                CHAT_EXECUTION_WORKFLOW,
                ChatExecutionRequest(execution_id, thread_id),
                id=execution_workflow_id(execution_id),
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except asyncio.CancelledError:
            # 스레드 자신이 취소된 것만 스레드를 끝내고, 실행 하나의 취소는 남은 줄을 계속 비운다.
            raise
        except Exception as error:
            workflow.logger.warning("chat execution did not settle: %s", error)
            return False
        return True
