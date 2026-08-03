"""title·recipe·cleanup 요청을 돌리고 종료 상태를 원장에 적은 뒤 완료 창구로 배달하는 액티비티다."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from temporalio import activity

from ...shared.agents.runtime.ledger import SqlSource
from ...shared.agents.runtime.notification import JobStatusNotifier
from ...shared.agents.shared.json_view import JsonObject, JsonValue, opt_text
from ...shared.agents.shared.models import (
    AgentExecutionRequest,
    AgentResponse,
    AgentStepDTO,
    CompletionCallback,
)
from ...shared.workflows.jobs_envelope import JobEnvelopeSource, JobExecutionEnvelope
from ...shared.workflows.jobs_kinds import AgentJobKind
from ...shared.workflows.jobs_ledger import JobLedger
from ...shared.workflows.jobs_spec import (
    FAIL_AGENT_JOB_ACTIVITY,
    FINALIZE_AGENT_JOB_ACTIVITY,
    GENERATE_AGENT_JOB_ACTIVITY,
    JOB_HEARTBEAT_INTERVAL_S,
    PREPARE_AGENT_JOB_ACTIVITY,
    SETTLE_CANCELED_JOB_ACTIVITY,
    AgentJobRequest,
    AgentJobSettlement,
)
from ..agents.recipe_scan.agent import RECIPE_SCAN_JOB
from ..agents.runtime.checkpoint import GraphCheckpointProvider
from ..agents.runtime.execution.completion import deliver_completion
from ..agents.runtime.execution.runner import execute
from ..agents.runtime.execution.trace import ExecutionTrace
from ..agents.runtime.job_agent import JobAgent
from ..agents.runtime.llm.client import ChatPair, make_chat_pair
from ..agents.runtime.pricing import ModelRates
from ..agents.runtime.tracer_client import TracerApiClient, TracerApiPort
from ..agents.shared.prompt_source_port import AgentPrompt
from ..agents.task_cleanup.agent import TASK_CLEANUP_JOB
from ..agents.title_suggestion.agent import TITLE_SUGGESTION_JOB
from .jobs_outcome import job_usage, status_and_error
from .jobs_writer import JobExecutionWriter, JobOutcome

JOB_AGENTS: dict[AgentJobKind, JobAgent[Any]] = {
    job.kind: job for job in (TITLE_SUGGESTION_JOB, TASK_CLEANUP_JOB, RECIPE_SCAN_JOB)
}


class AgentJobActivities:
    """워커가 열어 둔 추적 창구와 자기 원장으로 잡 셋을 그래프째 실행하는 액티비티 하나를 낸다."""

    def __init__(
        self,
        tracer_api_url: str,
        http_client: httpx.AsyncClient,
        execution_sql: SqlSource,
        prompts: Mapping[str, AgentPrompt],
        envelopes: JobEnvelopeSource | None = None,
        notifier: JobStatusNotifier | None = None,
        checkpoints: GraphCheckpointProvider | None = None,
        make_chats: Callable[[AgentExecutionRequest], ChatPair] = make_chat_pair,
    ) -> None:
        self._tracer_api_url = tracer_api_url
        self._http = http_client
        self._execution_sql = execution_sql
        self._prompts = prompts
        self._envelopes = envelopes
        self._notifier = notifier
        self._checkpoints = checkpoints
        self._make_chats = make_chats

    @activity.defn(name=PREPARE_AGENT_JOB_ACTIVITY)
    async def prepare(self, request: AgentJobRequest) -> JsonObject:
        """도메인 문맥을 모아 실행 입력에 싣고 원장을 실행 중으로 옮긴다."""
        job = JOB_AGENTS[request.kind]
        user_id = opt_text(request.payload.get("userId"))
        if not user_id:
            raise ValueError("agent job request has no user id to collect context for")
        prepared = await job.collect_context(request.payload, self._tracer(user_id))
        execution_id = opt_text(prepared.get("executionId"))
        if execution_id:
            async with self._execution_sql.connect() as sql:
                await JobLedger(sql).mark_running(execution_id, datetime.now(UTC))
            await self._notify(request.kind, execution_id, user_id, "running", _task_id_of(prepared))
        return prepared

    @activity.defn(name=GENERATE_AGENT_JOB_ACTIVITY)
    async def generate(self, request: AgentJobRequest) -> JsonObject:
        """이 시도가 쓸 봉투를 받아 그래프를 돌리며 자격을 이 액티비티 밖으로 내보내지 않는다."""
        payload = await self._resolve_payload(request)
        job = JOB_AGENTS[request.kind]
        tracer = self._tracer(str(payload["userId"]))
        req = await job.prepare(payload, tracer)
        heartbeat = asyncio.ensure_future(_heartbeat())
        try:
            response = await self._run_once(job, req, tracer)
        finally:
            heartbeat.cancel()
        cost_usd = ModelRates(req.modelRates).estimate_cost_usd(response.modelUsed, response.usage)
        return {
            "outcome": _outcome_payload(req, response, cost_usd),
            "response": response.model_dump(mode="json"),
        }

    @activity.defn(name=FINALIZE_AGENT_JOB_ACTIVITY)
    async def finalize(self, settlement: AgentJobSettlement) -> None:
        """생성이 낸 결과를 원장에 종결로 적고 산출물과 완료를 배달한다."""
        outcome = _outcome_of(settlement.outcome)
        if outcome.job_id:
            async with self._execution_sql.connect() as sql:
                await JobExecutionWriter(sql).finalize(outcome, datetime.now(UTC))
            await self._notify(
                settlement.kind,
                outcome.job_id,
                outcome.user_id,
                outcome.status,
                _task_id_of(settlement.payload),
            )
        response = AgentResponse.model_validate(settlement.response)
        if outcome.status == "completed":
            job = JOB_AGENTS[settlement.kind]
            await job.settle_outputs(self._tracer(outcome.user_id), outcome.job_id, response.data)
        await deliver_completion(self._http, _callback_of(settlement.payload), response)

    @activity.defn(name=FAIL_AGENT_JOB_ACTIVITY)
    async def fail(self, request: AgentJobRequest, message: str) -> None:
        """어느 단계가 실패하든 원장을 실패로 닫으며, 이미 종결된 행은 조건부 갱신이 그대로 둔다."""
        execution_id = opt_text(request.payload.get("executionId"))
        if not execution_id:
            return
        async with self._execution_sql.connect() as sql:
            await JobLedger(sql).settle(execution_id, "failed", {}, {}, message[:2000], datetime.now(UTC))
        user_id = opt_text(request.payload.get("userId"))
        if user_id:
            await self._notify(request.kind, execution_id, user_id, "failed", _task_id_of(request.payload))

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
        payload: JsonObject = {"jobId": job_id, "kind": kind.wire, "status": status}
        if task_id is not None:
            payload["taskId"] = task_id
        await self._notifier.job_updated(user_id, payload)

    def _tracer(self, user_id: str) -> TracerApiPort:
        """이 실행이 볼 추적 창구를 그 사용자 범위로 묶어 낸다."""
        return TracerApiClient(self._http, self._tracer_api_url, user_id)

    async def _resolve_payload(self, request: AgentJobRequest) -> JsonObject:
        """자격이 있으면 그대로 쓰고, 없으면 잡 종류와 사용자로 이 시도가 쓸 봉투를 가져온다."""
        if _has_credentials(request.payload):
            return request.payload
        if self._envelopes is None:
            raise ValueError("agent job request has no credentials and no envelope source is wired")
        user_id = opt_text(request.payload.get("userId"))
        if not user_id:
            raise ValueError("agent job request has no user id to pull an envelope for")
        envelope = await self._envelopes.issue(request.kind.wire, user_id)
        return merge_envelope(request.payload, envelope)

    async def _run_once(
        self, job: JobAgent[Any], req: AgentExecutionRequest, tracer: TracerApiPort
    ) -> AgentResponse:
        """그래프 하나를 실행 궤적과 데드라인 안에서 돌려 이 시도의 응답을 낸다."""
        prompt = self._prompts[job.kind]

        async def body(trace: ExecutionTrace) -> dict[str, JsonValue]:
            return await job.run(req, tracer, trace, prompt, self._checkpoints, self._make_chats(req))

        return await execute(
            job.kind,
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
            prompt_version=prompt.version(),
            tool_contract_version=prompt.tool_contract_version,
        )


def _task_id_of(payload: JsonObject) -> str | None:
    """태스크에 매인 잡만 그 식별자를 알림에 싣는다."""
    task_id = payload.get("taskId")
    return task_id if isinstance(task_id, str) else None


def _callback_of(payload: JsonObject) -> CompletionCallback | None:
    """완료를 되돌려 받을 창구가 실렸을 때만 그 창구를 낸다."""
    callback = payload.get("completionCallback")
    return None if not isinstance(callback, dict) else CompletionCallback.model_validate(callback)


def _outcome_payload(
    req: AgentExecutionRequest, response: AgentResponse, cost_usd: float | None
) -> JsonObject:
    """생성이 낸 결과를 종결이 원장에 적을 값으로 옮기며 자격을 싣지 않는다."""
    status, error = status_and_error(response)
    attempt = _attempt(req.attemptId)
    return {
        "jobId": req.executionId or "",
        "userId": req.userId,
        "status": status,
        "attempt": attempt,
        "result": response.data or {},
        "usage": job_usage(response, cost_usd, attempt),
        "error": error,
        "steps": [step.model_dump(mode="json") for step in response.steps],
        "observation": (
            None if response.observation is None else response.observation.model_dump(mode="json")
        ),
    }


def _outcome_of(payload: JsonObject) -> JobOutcome:
    """생성이 실어 보낸 종결 값을 원장이 받는 모양으로 되돌린다."""
    steps = payload.get("steps")
    observation = payload.get("observation")
    return JobOutcome(
        job_id=str(payload["jobId"]),
        user_id=str(payload["userId"]),
        status=str(payload["status"]),
        attempt=int(payload["attempt"]),  # type: ignore[arg-type]
        result=dict(payload["result"]),  # type: ignore[arg-type]
        usage=dict(payload["usage"]),  # type: ignore[arg-type]
        error=opt_text(payload.get("error")) or None,
        steps=[AgentStepDTO.model_validate(step) for step in steps] if isinstance(steps, list) else [],
        observation=dict(observation) if isinstance(observation, dict) else None,
    )


def _attempt(attempt_id: str | None) -> int:
    """궤적의 시도 회차이며 요청이 회차를 싣지 않으면 첫 시도로 본다."""
    return int(attempt_id) if attempt_id is not None and attempt_id.isdigit() else 1


def merge_envelope(payload: JsonObject, envelope: JobExecutionEnvelope) -> JsonObject:
    """자격과 단가와 한도와 데드라인을 봉투에서 받아 이번 시도의 실행 입력에 싣는다."""
    merged: JsonObject = {
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


def _has_credentials(payload: JsonObject) -> bool:
    api_key = payload.get("apiKey")
    return isinstance(api_key, str) and len(api_key) > 0


def _has_model(payload: JsonObject) -> bool:
    model = payload.get("model")
    return isinstance(model, str) and len(model) > 0


async def _heartbeat() -> None:
    while True:
        await asyncio.sleep(JOB_HEARTBEAT_INTERVAL_S)
        activity.heartbeat()
