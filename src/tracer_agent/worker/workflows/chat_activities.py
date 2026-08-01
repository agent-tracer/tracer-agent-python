"""Temporal 액티비티를 chat 실행의 준비와 생성과 종결과 실패에 잇고 취소를 모델 호출까지 전파한다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ...shared.agents.chat.execution_ledger import CLAIMED, THREAD_BUSY, ChatExecutionLedger
from ...shared.agents.chat.models import ChatRequest
from ...shared.agents.runtime.ledger import LedgerSql, SqlSource
from ...shared.agents.runtime.wakeup import UpdatePublisher
from ...shared.agents.shared.models import ResolvedPromptTemplateHashDTO
from ...shared.workflows.chat_spec import (
    FAIL_ACTIVITY,
    FINALIZE_ACTIVITY,
    GENERATE_ACTIVITY,
    HEARTBEAT_INTERVAL_S,
    PREPARE_ACTIVITY,
    RUNNING_LEASE_S,
    THREAD_BUSY_FAILURE,
    ChatExecutionRequest,
    FailedChatExecution,
    GeneratedChatExecution,
    PreparedChatExecution,
)
from ..agents.chat.agent import AGENT_NAME, run_chat
from ..agents.chat.checkpoint import ChatCheckpointProvider
from ..agents.chat.execution_writer import ChatExecutionWriter
from ..agents.chat.prompts import PROMPT_VERSION, SYSTEM_TEMPLATE, build_system_prompt
from ..agents.runtime.execution.runner import AgentBody, execute
from ..agents.runtime.execution.trace import ExecutionTrace
from ..agents.shared.prompt_source_port import AgentPrompt
from ..agents.shared.resolved_prompt_hash import ResolvedPromptBundleHash, resolved_prompt_bundle_hash
from .chat_turn import (
    canceled_turn,
    completed_turn,
    ledger_outcome,
    raise_for_error,
    turn_request,
)
from .envelope import ChatEnvelopeSource

# 열려 있는 실행이 없으면 재시도해도 같은 답이 오므로 워크플로를 더 태우지 않는다.
_NOT_ACTIVE = "chat.execution-not-active"


class ChatExecutionActivities:
    """실행 원장과 모델 호출을 액티비티 넷으로 묶어 워크플로가 부를 표면을 만든다."""

    def __init__(
        self,
        sql: SqlSource,
        http_client: httpx.AsyncClient,
        checkpoints: ChatCheckpointProvider,
        envelopes: ChatEnvelopeSource,
        prompt: AgentPrompt,
        wakeup: UpdatePublisher | None = None,
    ) -> None:
        self._sql = sql
        self._http = http_client
        self._checkpoints = checkpoints
        self._envelopes = envelopes
        self._wakeup = wakeup
        self._prompt = prompt
        self._system_prompt = build_system_prompt(prompt)

    def _resolved_prompt(self) -> ResolvedPromptBundleHash:
        """이번 실행이 조립한 시스템 프롬프트의 해시이며 관측이 그것을 싣는다."""
        return resolved_prompt_bundle_hash({SYSTEM_TEMPLATE: self._system_prompt})

    @activity.defn(name=PREPARE_ACTIVITY)
    async def prepare(self, request: ChatExecutionRequest) -> PreparedChatExecution:
        """대기 실행 하나를 running 자리로 옮기고 이번 턴이 쓸 사실을 낸다."""
        async with self._sql.connect() as sql:
            row = await self._claim(sql, request)
        await self._publish(request.execution_id)
        return PreparedChatExecution(
            execution_id=request.execution_id,
            thread_id=str(row["thread_id"]),
            user_id=str(row["user_id"]),
            language=str(row["language"] or "auto"),
            model=None if row["model"] is None else str(row["model"]),
        )

    @activity.defn(name=GENERATE_ACTIVITY)
    async def generate(self, prepared: PreparedChatExecution) -> GeneratedChatExecution:
        """모델을 돌려 한 턴의 산출물을 만들고 취소로 끊겨도 그때까지의 것을 낸다."""
        attempt = activity.info().attempt
        # 단가도 한도도 자격도 이 서비스가 지어내지 않으므로 시도마다 서버에서 받아 봉투를 세운다.
        envelope = await self._envelopes.issue(prepared.execution_id, attempt)
        request = turn_request(prepared, envelope.fields)
        await self._begin_attempt(prepared, attempt, envelope.draft_token_hash)
        traces: list[ExecutionTrace] = []
        resolved = self._resolved_prompt()
        heartbeat = asyncio.ensure_future(_heartbeat())
        try:
            response = await execute(
                AGENT_NAME,
                request.model,
                request.deadlineMs,
                _body(traces, request, self._http, self._checkpoints, self._prompt),
                None,
                None,
                f"{prepared.execution_id}:{attempt}",
                execution_id=prepared.execution_id,
                attempt_id=str(attempt),
                prompt_version=PROMPT_VERSION,
                resolved_prompt_hash=resolved.resolved_prompt_hash,
                resolved_prompt_hashes=[
                    ResolvedPromptTemplateHashDTO(
                        templateKey=item.template_key, contentHash=item.content_hash
                    )
                    for item in resolved.resolved_prompt_hashes
                ],
            )
        except asyncio.CancelledError:
            # 취소된 턴도 그때까지 모델이 쓴 답변과 궤적을 남겨야 화면에 보인 것이 사라지지 않는다.
            return canceled_turn(prepared, attempt, request, _trace(traces))
        finally:
            heartbeat.cancel()
        if response.observation is not None and response.observation.status == "cancelled":
            return canceled_turn(prepared, attempt, request, _trace(traces))
        if response.error is not None and response.observation is not None:
            async with self._sql.connect() as sql:
                await ChatExecutionWriter(sql).record_observation(
                    prepared.execution_id,
                    prepared.user_id,
                    attempt,
                    response.observation.model_dump(mode="json"),
                    _now(),
                )
        raise_for_error(response)
        return completed_turn(prepared, attempt, request, response)

    @activity.defn(name=FINALIZE_ACTIVITY)
    async def finalize(self, generated: GeneratedChatExecution) -> None:
        """턴의 산출물을 실행 전이와 스레드 갱신과 함께 한 트랜잭션으로 적는다."""
        # 취소가 남긴 것이 빈 답변뿐이면 적재할 산출물이 없으므로 실행을 그대로 둔다.
        if generated.canceled and not generated.text.strip():
            return
        async with self._sql.connect() as sql:
            await ChatExecutionWriter(sql, self._wakeup).finalize(ledger_outcome(generated), _now())

    @activity.defn(name=FAIL_ACTIVITY)
    async def fail(self, request: FailedChatExecution) -> None:
        """아직 살아 있는 실행을 사유와 함께 실패로 닫는다."""
        async with self._sql.connect() as sql:
            closed = await ChatExecutionLedger(sql).fail_active(request.execution_id, request.error, _now())
        if closed:
            await self._publish(request.execution_id)

    async def _claim(self, sql: LedgerSql, request: ChatExecutionRequest) -> dict[str, Any]:
        ledger = ChatExecutionLedger(sql)
        row = await ledger.find_by_id(request.execution_id)
        if row is None:
            raise ApplicationError("chat execution not found", type=_NOT_ACTIVE, non_retryable=True)
        if row["status"] == "queued":
            await self._take_running_seat(ledger, request)
            row = await ledger.find_by_id(request.execution_id)
        if row is None or row["status"] != "running":
            raise ApplicationError("chat execution is not active", type=_NOT_ACTIVE, non_retryable=True)
        return row

    async def _take_running_seat(self, ledger: ChatExecutionLedger, request: ChatExecutionRequest) -> None:
        if await ledger.claim_queued(request.execution_id, _now()) != THREAD_BUSY:
            return
        # 스레드를 막은 running이 갱신을 멈춘 것이면 되돌리고 다시 집으며, 살아 있으면 물러난다.
        now = _now()
        recovered = await ledger.recover_stale_running(
            now - timedelta(seconds=RUNNING_LEASE_S), now, request.thread_id
        )
        if recovered == 0 or await ledger.claim_queued(request.execution_id, _now()) != CLAIMED:
            raise ApplicationError(
                f"chat thread {request.thread_id} is busy", type=THREAD_BUSY_FAILURE, non_retryable=True
            )

    async def _begin_attempt(
        self, prepared: PreparedChatExecution, attempt: int, draft_token_hash: str
    ) -> None:
        async with self._sql.connect() as sql:
            opened = await ChatExecutionLedger(sql).begin_attempt(
                prepared.execution_id, attempt, draft_token_hash, _now()
            )
        if not opened:
            raise RuntimeError("chat execution attempt is stale")

    async def _publish(self, execution_id: str) -> None:
        if self._wakeup is not None:
            await self._wakeup.publish(execution_id, {"executionId": execution_id})


def _now() -> datetime:
    return datetime.now(UTC)


def _trace(traces: list[ExecutionTrace]) -> ExecutionTrace:
    return traces[0] if traces else ExecutionTrace()


async def _heartbeat() -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        activity.heartbeat()


def _body(
    traces: list[ExecutionTrace],
    request: ChatRequest,
    http_client: httpx.AsyncClient,
    checkpoints: ChatCheckpointProvider,
    prompt: AgentPrompt,
) -> AgentBody:
    async def run(trace: ExecutionTrace) -> dict[str, object]:
        # 취소가 걸려도 그때까지의 궤적을 읽을 수 있도록 실행이 쓰는 궤적을 붙잡아 둔다.
        traces.append(trace)
        return await run_chat(request, http_client, trace, prompt, checkpoints)

    return run
