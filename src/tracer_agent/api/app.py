"""잡과 대화의 접수와 조회와 취소를, 그리고 배포 단위 사이의 내부 창구를 계약이 정한 표면으로 노출한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from ..shared.agents.chat.intake.router import (
    ACCEPTED_STATUS,
    CHAT_CANCEL_PATH,
    CHAT_MESSAGES_PATH,
    cancel_chat_turn,
    enqueue_chat_turn,
)
from ..shared.agents.envelope.router import (
    CHAT_ENVELOPE_PATH,
    JOB_ENVELOPE_PATH,
    issue_chat_execution_envelope,
    issue_job_execution_envelope,
)
from ..shared.agents.prompt_registry.router import (
    CREATED_STATUS,
    PROMPT_FRAGMENTS_REGISTER_PATH,
    PROMPT_REGISTER_PATH,
    register_and_resolve_prompt_fragments,
    register_prompt,
)
from ..shared.agents.runtime.ledger import LedgerPoolProvider, PooledSql
from ..shared.agents.runtime.telemetry.bootstrap import configure_observability
from ..shared.agents.runtime.wakeup import UpdatePublisher
from ..shared.agents.settings.router import (
    SETTING_MODELS_PATH,
    SETTING_PATH,
    SETTINGS_PATH,
    delete_setting,
    list_setting_models,
    list_settings,
    put_setting,
)
from ..shared.agents.settings.secret import SettingCipher
from ..shared.config import get_settings
from ..shared.workflows.chat_spec import CHAT_EXECUTION_UPDATES_TOPIC
from ..shared.workflows.dispatch import TemporalClientProvider, TemporalExecutionDispatch
from ..shared.workflows.jobs_dispatch import TemporalJobDispatch
from ..shared.workflows.jobs_envelope import JobEnvelopeClient
from ..shared.workflows.jobs_intake import JOB_CANCEL_PATH, JOBS_PATH, cancel_job, enqueue_job
from ..shared.workflows.jobs_query import JOB_PATH, JOB_STEPS_PATH, get_job, get_job_steps
from .credentials import SettingModelCredentials
from .evaluation import run_evaluation

# 접수가 잡 종류의 카탈로그 값을 물을 때 쓰는 여유이며 실행 자체를 기다리지 않는다.
ENVELOPE_HTTP_TIMEOUT_S = 20.0


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.configure_langsmith()
    shutdown_observability = configure_observability()
    application.state.executions = LedgerPoolProvider(settings.execution_dsn())
    application.state.execution_sql = PooledSql(application.state.executions)
    encryption_key = settings.monitor_settings_encryption_key
    application.state.setting_cipher = SettingCipher(
        None if encryption_key is None else encryption_key.get_secret_value(),
        settings.monitor_profile,
    )
    application.state.model_credentials = SettingModelCredentials(
        application.state.execution_sql, application.state.setting_cipher
    )
    application.state.read_api_base_url = settings.tracer_api_url
    application.state.agent_api_base_url = settings.agent_api_url
    application.state.execution_updates = UpdatePublisher(
        settings.kafka_brokers, CHAT_EXECUTION_UPDATES_TOPIC
    )
    temporal_client = TemporalClientProvider(settings.connect_temporal)
    application.state.execution_dispatch = TemporalExecutionDispatch(temporal_client)
    application.state.job_dispatch = TemporalJobDispatch(temporal_client)
    application.state.job_envelope_http = httpx.AsyncClient(timeout=ENVELOPE_HTTP_TIMEOUT_S)
    application.state.job_envelopes = JobEnvelopeClient(
        application.state.job_envelope_http, settings.agent_api_url
    )
    try:
        yield
    finally:
        shutdown_observability()
        await application.state.job_envelope_http.aclose()
        await application.state.execution_updates.close()
        await application.state.executions.close()


async def health() -> dict[str, str]:
    return {"status": "ok"}


def create_app() -> FastAPI:
    """독립 수명을 가진 에이전트 HTTP 앱을 만든다."""
    application = FastAPI(title="tracer-agent", lifespan=lifespan)
    application.get("/health")(health)
    application.post("/v1/evaluation-runs")(run_evaluation)
    # 브라우저가 백엔드마다 다른 경로를 치지 않도록 계약이 정한 경로로 연다.
    application.post(CHAT_MESSAGES_PATH, status_code=ACCEPTED_STATUS)(enqueue_chat_turn)
    application.post(CHAT_CANCEL_PATH)(cancel_chat_turn)
    application.post(JOBS_PATH, status_code=ACCEPTED_STATUS)(enqueue_job)
    application.post(JOB_CANCEL_PATH)(cancel_job)
    application.get(JOB_PATH)(get_job)
    application.get(JOB_STEPS_PATH)(get_job_steps)
    application.get(SETTINGS_PATH)(list_settings)
    application.get(SETTING_MODELS_PATH)(list_setting_models)
    application.put(SETTING_PATH)(put_setting)
    application.delete(SETTING_PATH)(delete_setting)
    # 배포 단위 사이에서만 오가는 창구라 게이트웨이가 바깥에 열지 않는다.
    application.post(PROMPT_FRAGMENTS_REGISTER_PATH)(register_and_resolve_prompt_fragments)
    application.post(PROMPT_REGISTER_PATH, status_code=CREATED_STATUS)(register_prompt)
    application.post(CHAT_ENVELOPE_PATH)(issue_chat_execution_envelope)
    application.post(JOB_ENVELOPE_PATH)(issue_job_execution_envelope)
    return application


app = create_app()
