"""title·recipe·cleanup 요청을 돌리고 종료 상태를 원장에 적은 뒤 완료 창구로 배달하는 액티비티다."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from temporalio import activity
from temporalio.exceptions import is_cancelled_exception

from ...shared.agents.recipe_scan.models import RecipeScanRequest
from ...shared.agents.runtime.ledger import SqlSource
from ...shared.agents.runtime.notification import JobStatusNotifier
from ...shared.agents.shared.models import AgentResponse
from ...shared.agents.task_cleanup.models import TaskCleanupRequest
from ...shared.agents.title_suggestion.models import TitleSuggestionRequest
from ...shared.workflows.jobs_envelope import JobEnvelopeSource, JobExecutionEnvelope
from ...shared.workflows.jobs_kinds import wire_kind
from ...shared.workflows.jobs_ledger import JobLedger
from ...shared.workflows.jobs_spec import (
    JOB_HEARTBEAT_INTERVAL_S,
    RUN_AGENT_JOB_ACTIVITY,
    SETTLE_CANCELED_JOB_ACTIVITY,
    AgentJobKind,
    AgentJobRequest,
)
from ..agents.recipe_scan.agent import run_recipe_scan
from ..agents.recipe_scan.prompts import PROMPT_VERSION as RECIPE_PROMPT_VERSION
from ..agents.runtime.execution.completion import run_and_deliver
from ..agents.runtime.execution.runner import AgentBody, execute
from ..agents.runtime.execution.trace import ExecutionTrace
from ..agents.runtime.outputs import deliver_job_outputs
from ..agents.runtime.pricing import ModelRates
from ..agents.runtime.tracer_client import TracerApiClient
from ..agents.task_cleanup.agent import run_task_cleanup
from ..agents.task_cleanup.prompts import PROMPT_VERSION as CLEANUP_PROMPT_VERSION
from ..agents.task_cleanup.reader import load_cleanup_batch
from ..agents.title_suggestion.agent import run_title_suggestion
from ..agents.title_suggestion.prompts import PROMPT_VERSION as TITLE_PROMPT_VERSION
from ..agents.title_suggestion.reader import load_title_context
from .jobs_outcome import job_usage, status_and_error
from .jobs_writer import JobExecutionWriter, JobOutcome

JobRequest = TitleSuggestionRequest | RecipeScanRequest | TaskCleanupRequest


class AgentJobActivities:
    """워커가 열어 둔 추적 창구와 자기 원장으로 잡 셋을 그래프째 돌리는 액티비티 하나를 낸다."""

    def __init__(
        self,
        tracer_api_url: str,
        http_client: httpx.AsyncClient,
        execution_sql: SqlSource,
        prompt_fragments: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
        envelopes: JobEnvelopeSource | None = None,
        notifier: JobStatusNotifier | None = None,
    ) -> None:
        self._tracer_api_url = tracer_api_url
        self._http = http_client
        self._execution_sql = execution_sql
        self._prompt_fragments = prompt_fragments
        self._envelopes = envelopes
        self._notifier = notifier

    @activity.defn(name=RUN_AGENT_JOB_ACTIVITY)
    async def run(self, request: AgentJobRequest) -> None:
        """요청 종류에 맞는 그래프를 돌리고 결과를 완료 창구로 배달한다."""
        payload = await self._resolve_payload(request)
        heartbeat = asyncio.ensure_future(_heartbeat())
        try:
            await self._dispatch(request.kind, payload)
        except BaseException as error:
            # 그래프를 돌리기 전에 죽으면 원장이 running도 못 거쳐 대기 중에 그대로 남는다.
            if not is_cancelled_exception(error):
                await self._settle_failed_before_dispatch(request, error)
            raise
        finally:
            heartbeat.cancel()

    async def _settle_failed_before_dispatch(self, request: AgentJobRequest, error: BaseException) -> None:
        """그래프 실행 전 실패를 원장에 닫으며, 이미 종결된 행은 조건부 갱신이 그대로 둔다."""
        execution_id = request.payload.get("executionId")
        if not isinstance(execution_id, str) or not execution_id:
            return
        async with self._execution_sql.connect() as sql:
            await JobLedger(sql).settle(execution_id, "failed", {}, {}, str(error)[:2000], datetime.now(UTC))
        user_id = request.payload.get("userId")
        if isinstance(user_id, str) and user_id:
            task_id = request.payload.get("taskId")
            await self._notify(
                request.kind, execution_id, user_id, "failed", task_id if isinstance(task_id, str) else None
            )

    @activity.defn(name=SETTLE_CANCELED_JOB_ACTIVITY)
    async def settle_canceled(self, execution_id: str) -> None:
        """실행 액티비티가 못 돈 취소를 원장에서 닫으며, 이미 종결된 행은 조건부 갱신이 그대로 둔다."""
        async with self._execution_sql.connect() as sql:
            await JobLedger(sql).settle(
                execution_id, "canceled", {}, {}, "canceled before execution started", datetime.now(UTC)
            )

    async def _notify(
        self, kind: AgentJobKind, job_id: str, user_id: str, status: str, task_id: str | None
    ) -> None:
        """잡 상태 전이를 알림 토픽에 실으며 발행자가 없으면 아무 일도 하지 않는다."""
        if self._notifier is None:
            return
        payload: dict[str, Any] = {"jobId": job_id, "kind": wire_kind(kind), "status": status}
        if task_id is not None:
            payload["taskId"] = task_id
        await self._notifier.job_updated(user_id, payload)

    def _tracer(self, user_id: str) -> TracerApiClient:
        """이 실행이 볼 추적 창구를 그 사용자 범위로 묶어 낸다."""
        return TracerApiClient(self._http, self._tracer_api_url, user_id)

    async def _dispatch(self, kind: AgentJobKind, payload: dict[str, Any]) -> None:
        """요청 종류에 맞는 그래프를 골라 돌린다."""
        tracer = self._tracer(str(payload["userId"]))
        if kind == "title-suggestion":
            if "context" not in payload:
                # 접수가 SDK 축을 거치지 않고 직접 받은 요청이라 컨텍스트를 이 시점에 스스로 조립한다.
                context = await load_title_context(tracer, payload["taskId"])
                payload = {**payload, "context": context.model_dump(mode="json")}
            title_req = TitleSuggestionRequest.model_validate(payload)

            async def title_body(
                trace: ExecutionTrace, req: TitleSuggestionRequest = title_req
            ) -> dict[str, object]:
                return await run_title_suggestion(req, tracer, trace, self._prompt_fragments)

            await self._run_and_deliver(kind, title_req, title_body, TITLE_PROMPT_VERSION)
            return
        if kind == "task-cleanup":
            if "batch" not in payload:
                # 접수가 SDK 축을 거치지 않고 직접 받은 요청이라 후보 배치를 이 시점에 스스로 조립한다.
                now = datetime.now(UTC)
                batch = await load_cleanup_batch(tracer, now)
                payload = {
                    **payload,
                    "batch": batch.model_dump(mode="json"),
                    "scannedAt": now.isoformat(),
                }
            cleanup_req = TaskCleanupRequest.model_validate(payload)

            async def cleanup_body(
                trace: ExecutionTrace, req: TaskCleanupRequest = cleanup_req
            ) -> dict[str, object]:
                return await run_task_cleanup(req, tracer, trace, self._prompt_fragments)

            await self._run_and_deliver(kind, cleanup_req, cleanup_body, CLEANUP_PROMPT_VERSION)
            return
        recipe_req = RecipeScanRequest.model_validate(payload)

        async def recipe_body(
            trace: ExecutionTrace, req: RecipeScanRequest = recipe_req
        ) -> dict[str, object]:
            return await run_recipe_scan(req, tracer, trace, self._prompt_fragments)

        await self._run_and_deliver(kind, recipe_req, recipe_body, RECIPE_PROMPT_VERSION)

    async def _resolve_payload(self, request: AgentJobRequest) -> dict[str, Any]:
        """자격이 있으면 그대로 쓰고, 없으면 잡 종류와 사용자로 이 시도가 쓸 봉투를 당겨온다."""
        if _has_credentials(request.payload):
            return request.payload
        if self._envelopes is None:
            raise ValueError("agent job request has no credentials and no envelope source is wired")
        user_id = request.payload.get("userId")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("agent job request has no user id to pull an envelope for")
        envelope = await self._envelopes.issue(wire_kind(request.kind), user_id)
        return merge_envelope(request.payload, envelope)

    async def _run_and_deliver(
        self,
        kind: AgentJobKind,
        req: JobRequest,
        body: AgentBody,
        prompt_version: str,
    ) -> None:
        async def run_once() -> AgentResponse:
            return await execute(
                kind,
                req.model,
                req.deadlineMs,
                body,
                req.jobId,
                req.idempotencyKey,
                None,
                None,
                req.idempotency_input_hash(),
                req.executionId,
                req.attemptId,
                prompt_version,
            )

        if req.executionId is not None:
            async with self._execution_sql.connect() as sql:
                await JobLedger(sql).mark_running(req.executionId, datetime.now(UTC))
            await self._notify(kind, req.executionId, req.userId, "running", _task_id(req))

        async def settle(response: AgentResponse) -> None:
            if req.executionId is None:
                return
            status, error = status_and_error(response)
            cost_usd = ModelRates(req.modelRates).estimate_cost_usd(response.modelUsed, response.usage)
            outcome = JobOutcome(
                job_id=req.executionId,
                user_id=req.userId,
                status=status,
                attempt=_attempt(req.attemptId),
                result=response.data or {},
                usage=job_usage(response, cost_usd, _attempt(req.attemptId)),
                error=error,
                steps=response.steps,
                observation=(
                    None if response.observation is None else response.observation.model_dump(mode="json")
                ),
            )
            async with self._execution_sql.connect() as sql:
                await JobExecutionWriter(sql).finalize(outcome, datetime.now(UTC))
            await self._notify(kind, req.executionId, req.userId, status, _task_id(req))
            if status == "completed":
                await deliver_job_outputs(self._tracer(req.userId), kind, req.executionId, response.data)

        await run_and_deliver(self._http, req.completionCallback, run_once, settle)


def _task_id(req: JobRequest) -> str | None:
    """태스크에 매인 잡만 그 식별자를 알림에 싣는다."""
    task_id = getattr(req, "taskId", None)
    return task_id if isinstance(task_id, str) else None


def _attempt(attempt_id: str | None) -> int:
    """궤적의 시도 회차이며 요청이 회차를 싣지 않으면 첫 시도로 본다."""
    return int(attempt_id) if attempt_id is not None and attempt_id.isdigit() else 1


def merge_envelope(payload: dict[str, Any], envelope: JobExecutionEnvelope) -> dict[str, Any]:
    """자격과 단가와 한도와 데드라인을 봉투에서 받아 이번 시도의 실행 입력에 싣는다."""
    merged: dict[str, Any] = {
        **payload,
        "apiKey": envelope.api_key,
        "modelRates": envelope.model_rates,
        "limits": envelope.limits,
        "deadlineMs": envelope.deadline_ms,
    }
    if not _has_model(payload):
        merged["model"] = envelope.model
        merged["fallbackModel"] = envelope.fallback_model
    return merged


def _has_credentials(payload: dict[str, Any]) -> bool:
    api_key = payload.get("apiKey")
    return isinstance(api_key, str) and len(api_key) > 0


def _has_model(payload: dict[str, Any]) -> bool:
    model = payload.get("model")
    return isinstance(model, str) and len(model) > 0


async def _heartbeat() -> None:
    while True:
        await asyncio.sleep(JOB_HEARTBEAT_INTERVAL_S)
        activity.heartbeat()
