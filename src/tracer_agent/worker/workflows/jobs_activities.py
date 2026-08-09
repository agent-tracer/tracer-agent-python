"""title·recipe·cleanup 요청을 실행하고 종료 상태를 원장에 적은 뒤 완료 창구로 배달하는 액티비티다."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ...shared.agents.runtime.ledger import SqlSource
from ...shared.agents.runtime.notification import JobStatusNotifier
from ...shared.agents.settings.execution import (
    LANGUAGE_FIELD,
    MAX_SUGGESTIONS_FIELD,
    carries,
    normalize_language,
    normalize_max_suggestions,
    setting_key,
)
from ...shared.agents.settings.store import PlainSettingReader
from ...shared.agents.shared.json_view import JsonObject, JsonValue, opt_text
from ...shared.agents.shared.models import (
    AgentExecutionRequest,
    AgentResponse,
    CompletionCallback,
)
from ...shared.workflows.jobs_envelope import JobEnvelopeSource, JobExecutionEnvelope
from ...shared.workflows.jobs_input import MAX_SUGGESTIONS_CAP
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
    GeneratedAgentJob,
    JobOutcome,
)
from ..agents.recipe_scan.agent import RECIPE_SCAN_JOB
from ..agents.runtime.checkpoint import GraphCheckpointProvider
from ..agents.runtime.execution.completion import deliver_completion
from ..agents.runtime.execution.runner import ExecutionRequest, execute
from ..agents.runtime.execution.trace import ExecutionTrace
from ..agents.runtime.job_agent import JobGraphAgent
from ..agents.runtime.llm.client import ChatPair, make_chat_pair
from ..agents.runtime.outputs import JobOutputTargets
from ..agents.runtime.pricing import ModelRates
from ..agents.runtime.tracer_client import TracerApiClient, TracerApiPort
from ..agents.shared.prompt_source_port import AgentPrompt
from ..agents.task_cleanup.agent import TASK_CLEANUP_JOB
from ..agents.title_suggestion.agent import TITLE_SUGGESTION_JOB
from .jobs_outcome import job_usage, status_and_error
from .jobs_writer import JobExecutionWriter

JOB_AGENTS: dict[AgentJobKind, JobGraphAgent[Any, Any]] = {
    job.kind: job for job in (TITLE_SUGGESTION_JOB, TASK_CLEANUP_JOB, RECIPE_SCAN_JOB)
}

CANCELED_WITHOUT_SETTLEMENT = "canceled without a settlement from this attempt"
CLOSED_BEFORE_START = "job reached a terminal status before this attempt started"


def _blocked(kind: AgentJobKind | None, job_id: str, status: str) -> None:
    """종료 상태에 이미 닿은 행이 이 전이를 받지 않았다는 사실을 액티비티 문맥과 함께 남긴다."""
    activity.logger.warning(
        "agent.job.settle.blocked kind=%s jobId=%s status=%s",
        "unknown" if kind is None else kind.wire,
        job_id,
        status,
    )


class AgentJobActivities:
    """워커가 열어 둔 추적 창구와 자기 원장으로 잡 셋을 그래프째 실행하는 액티비티 하나를 낸다."""

    def __init__(
        self,
        tracer_api_url: str,
        agent_api_url: str,
        http_client: httpx.AsyncClient,
        execution_sql: SqlSource,
        prompts: Mapping[str, AgentPrompt],
        envelopes: JobEnvelopeSource | None = None,
        notifier: JobStatusNotifier | None = None,
        checkpoints: GraphCheckpointProvider | None = None,
        make_chats: Callable[[AgentExecutionRequest], ChatPair] = make_chat_pair,
    ) -> None:
        self._tracer_api_url = tracer_api_url
        self._agent_api_url = agent_api_url
        self._http = http_client
        self._execution_sql = execution_sql
        self._prompts = prompts
        self._envelopes = envelopes
        self._notifier = notifier
        self._checkpoints = checkpoints
        self._make_chats = make_chats

    @activity.defn(name=PREPARE_AGENT_JOB_ACTIVITY)
    async def prepare(self, request: AgentJobRequest) -> JsonObject:
        """도메인 문맥과 설정을 모아 실행 입력에 싣고 원장을 실행 중으로 옮긴다."""
        job = JOB_AGENTS[request.kind]
        user_id = opt_text(request.payload.get("userId"))
        if not user_id:
            raise ValueError("agent job request has no user id to collect context for")
        collected = await job.collect_context(request.payload, self._tracer(user_id))
        prepared = await self._carry_settings(request.kind, user_id, collected)
        execution_id = opt_text(prepared.get("executionId"))
        if execution_id:
            async with self._execution_sql.connect() as sql:
                running = await JobLedger(sql).mark_running(execution_id, datetime.now(UTC))
            if not running:
                _blocked(request.kind, execution_id, "running")
                raise ApplicationError(CLOSED_BEFORE_START, non_retryable=True)
            await self._notify(request.kind, execution_id, user_id, "running", _task_id_of(prepared))
        return prepared

    @activity.defn(name=GENERATE_AGENT_JOB_ACTIVITY)
    async def generate(self, request: AgentJobRequest) -> GeneratedAgentJob:
        """이 시도가 쓸 봉투를 받아 그래프를 실행하며 자격을 이 액티비티 밖으로 내보내지 않는다."""
        payload = await self._resolve_payload(request)
        job = JOB_AGENTS[request.kind]
        user_id = str(payload["userId"])
        tracer = self._tracer(user_id)
        req = await job.prepare(payload, tracer)
        heartbeat = asyncio.ensure_future(_heartbeat())
        try:
            response = await self._run_once(job, req, tracer, self._agent_api(user_id))
        finally:
            heartbeat.cancel()
        cost_usd = ModelRates(req.modelRates).estimate_cost_usd(
            response.actualModel or response.modelUsed, response.usage
        )
        return GeneratedAgentJob(outcome=_outcome(req, response, cost_usd), response=response)

    @activity.defn(name=FINALIZE_AGENT_JOB_ACTIVITY)
    async def finalize(self, settlement: AgentJobSettlement) -> None:
        """생성이 낸 결과를 원장에 종결로 적고 전이가 받아들여진 실행만 알림과 산출물을 낸다."""
        outcome = settlement.outcome
        settled = True
        if outcome.job_id:
            async with self._execution_sql.connect() as sql:
                settled = await JobExecutionWriter(sql).finalize(outcome, datetime.now(UTC))
            if settled:
                await self._notify(
                    settlement.kind,
                    outcome.job_id,
                    outcome.user_id,
                    outcome.status,
                    _task_id_of(settlement.payload),
                )
            else:
                _blocked(settlement.kind, outcome.job_id, outcome.status)
        if settled and outcome.status == "completed":
            job = JOB_AGENTS[settlement.kind]
            await job.settle_outputs(
                JobOutputTargets(self._execution_sql, self._tracer(outcome.user_id)),
                outcome.job_id,
                settlement.response.data,
                settlement.payload,
            )
        # 완료 콜백은 실행기에게 자기 토큰으로 종료를 알리는 자리이므로 원장 가드가 가르지 않는다.
        await deliver_completion(self._http, _callback_of(settlement.payload), settlement.response)

    @activity.defn(name=FAIL_AGENT_JOB_ACTIVITY)
    async def fail(self, request: AgentJobRequest, message: str) -> None:
        """어느 단계가 실패하든 원장을 실패로 닫으며, 이미 종결된 행은 조건부 갱신이 그대로 둔다."""
        execution_id = opt_text(request.payload.get("executionId"))
        if not execution_id:
            return
        async with self._execution_sql.connect() as sql:
            settled = await JobLedger(sql).settle(
                execution_id, "failed", {}, {}, message[:2000], datetime.now(UTC)
            )
        if not settled:
            _blocked(request.kind, execution_id, "failed")
            return
        user_id = opt_text(request.payload.get("userId"))
        if user_id:
            await self._notify(request.kind, execution_id, user_id, "failed", _task_id_of(request.payload))

    @activity.defn(name=SETTLE_CANCELED_JOB_ACTIVITY)
    async def settle_canceled(self, execution_id: str) -> None:
        """이번 시도가 종결을 내지 못한 채 끊긴 실행을 원장에서 닫으며, 이미 종결된 행은 그대로 둔다."""
        async with self._execution_sql.connect() as sql:
            settled = await JobLedger(sql).settle(
                execution_id, "canceled", {}, {}, CANCELED_WITHOUT_SETTLEMENT, datetime.now(UTC)
            )
        if not settled:
            _blocked(None, execution_id, "canceled")

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

    def _agent_api(self, user_id: str) -> TracerApiPort:
        """레시피 검색은 이 축이 소유한 원장을 보므로 자기 API 를 그 사용자 범위로 묶어 낸다."""
        return TracerApiClient(self._http, self._agent_api_url, user_id)

    async def _carry_settings(self, kind: AgentJobKind, user_id: str, payload: JsonObject) -> JsonObject:
        """계약이 설정에서 채우라고 적은 칸 가운데 요청이 비워 둔 것만 채운다."""
        carried: dict[str, JsonValue] = {}
        if LANGUAGE_FIELD not in payload and carries(LANGUAGE_FIELD, kind.wire):
            stored = await self._read_setting(user_id, setting_key(LANGUAGE_FIELD))
            carried[LANGUAGE_FIELD] = normalize_language(stored)
        if MAX_SUGGESTIONS_FIELD not in payload and carries(MAX_SUGGESTIONS_FIELD, kind.wire):
            stored = await self._read_setting(user_id, setting_key(MAX_SUGGESTIONS_FIELD))
            carried[MAX_SUGGESTIONS_FIELD] = normalize_max_suggestions(stored, MAX_SUGGESTIONS_CAP)
        return {**payload, **carried} if carried else payload

    async def _read_setting(self, user_id: str, key: str) -> str | None:
        """그 사용자가 저장해 둔 설정 하나이며 없으면 None 이다."""
        async with self._execution_sql.connect() as sql:
            return await PlainSettingReader(sql).read(user_id, key)

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
        self,
        job: JobGraphAgent[Any, Any],
        req: AgentExecutionRequest,
        tracer: TracerApiPort,
        agent_api: TracerApiPort,
    ) -> AgentResponse:
        """그래프 하나를 실행 궤적과 데드라인 안에서 실행해 이 시도의 응답을 낸다."""
        prompt = self._prompts[job.kind]

        async def body(trace: ExecutionTrace) -> dict[str, JsonValue]:
            return await job.run(
                req, tracer, trace, prompt, self._checkpoints, self._make_chats(req), agent_api
            )

        return await execute(
            ExecutionRequest(
                label=job.kind,
                model=req.model,
                deadline_ms=req.deadlineMs,
                prompt_version=prompt.version(),
                tool_contract_version=prompt.tool_contract_version,
                job_id=req.jobId,
                execution_id=req.executionId,
                attempt_id=req.attemptId,
                model_rates=req.modelRates,
            ),
            body,
        )


def _task_id_of(payload: JsonObject) -> str | None:
    """태스크에 매인 잡만 그 식별자를 알림에 싣는다."""
    task_id = payload.get("taskId")
    return task_id if isinstance(task_id, str) else None


def _callback_of(payload: JsonObject) -> CompletionCallback | None:
    """완료를 되돌려 받을 창구가 실렸을 때만 그 창구를 낸다."""
    callback = payload.get("completionCallback")
    return None if not isinstance(callback, dict) else CompletionCallback.model_validate(callback)


def _outcome(req: AgentExecutionRequest, response: AgentResponse, cost_usd: float | None) -> JobOutcome:
    """생성이 낸 결과를 종결이 원장에 적을 값으로 옮기며 자격을 싣지 않는다."""
    status, error = status_and_error(response)
    attempt = _attempt(req.attemptId)
    return JobOutcome(
        job_id=req.executionId or "",
        user_id=req.userId,
        status=status,
        attempt=attempt,
        result=response.data or {},
        usage=job_usage(response, cost_usd, attempt),
        error=error,
        steps=response.steps,
        observation=response.observation,
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
