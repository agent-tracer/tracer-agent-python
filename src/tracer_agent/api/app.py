"""잡과 대화의 접수와 조회와 취소를, 그리고 배포 단위 사이의 내부 창구를 계약이 정한 표면으로 노출한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..shared.agents.chat.intake.router import (
    ACCEPTED_STATUS,
    CHAT_CANCEL_PATH,
    CHAT_MESSAGES_PATH,
    CHAT_THREADS_PATH,
    cancel_chat_turn,
    enqueue_chat_turn,
)
from ..shared.agents.chat.surface.confirmations import (
    CHAT_CONFIRMATION_PATH,
    CHAT_CONFIRMATIONS_PATH,
    decide_chat_tool,
    propose_chat_tool,
)
from ..shared.agents.chat.surface.drafts import CHAT_DRAFTS_PATH, checkpoint_chat_draft
from ..shared.agents.chat.surface.envelope import CREATED_STATUS as CHAT_CREATED_STATUS
from ..shared.agents.chat.surface.executions import (
    CHAT_EXECUTION_REPLAY_PATH,
    CHAT_EXECUTION_STEPS_PATH,
    CHAT_EXECUTIONS_PATH,
    get_chat_replay,
    list_chat_execution_steps,
    list_chat_executions,
)
from ..shared.agents.chat.surface.memories import (
    CHAT_MEMORIES_PATH,
    CHAT_MEMORY_PATH,
    recall_chat_facts,
    remember_chat_fact,
)
from ..shared.agents.chat.surface.stream import CHAT_EXECUTION_EVENTS_PATH, watch_chat_execution
from ..shared.agents.chat.surface.threads import (
    CHAT_THREAD_MESSAGES_PATH,
    CHAT_THREAD_PATH,
    create_chat_thread,
    delete_chat_thread,
    get_chat_thread,
    list_chat_messages,
    list_chat_threads,
    rename_chat_thread,
)
from ..shared.agents.chat.surface.tool_client import HttpChatToolExecutor
from ..shared.agents.chat.surface.updates import UpdateSubscriber
from ..shared.agents.envelope.router import router as envelope_router
from ..shared.agents.runtime.ledger import LedgerPoolProvider, PooledSql, SqlSource
from ..shared.agents.runtime.telemetry.bootstrap import configure_observability
from ..shared.agents.runtime.wakeup import UpdatePublisher
from ..shared.agents.settings.router import router as settings_router
from ..shared.agents.settings.secret import SettingCipher
from ..shared.config import get_settings
from ..shared.workflows.chat_spec import CHAT_EXECUTION_UPDATES_TOPIC
from ..shared.workflows.dispatch import TemporalClientProvider, TemporalExecutionDispatch
from ..shared.workflows.jobs_anchor import RuleAnchorClient
from ..shared.workflows.jobs_dispatch import TemporalJobDispatch
from ..shared.workflows.jobs_envelope import JobEnvelopeClient
from ..shared.workflows.jobs_intake import router as job_intake_router
from ..shared.workflows.jobs_query import router as job_query_router
from .credentials import SettingModelCredentials
from .surface import SURFACE_PATH, get_served_surface

# 접수가 잡 종류의 카탈로그 값을 물을 때 쓰는 여유이며 실행 자체를 기다리지 않는다.
ENVELOPE_HTTP_TIMEOUT_S = 20.0

# 원장까지 왕복하는 질의라야 연결이 살아 있다는 것을 알린다.
READINESS_PROBE = "SELECT 1"
UNREADY_STATUS = 503


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.configure_langsmith()
    shutdown_observability = configure_observability()
    application.state.executions = LedgerPoolProvider(settings.agent_dsn())
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
    application.state.execution_watch = UpdateSubscriber(settings.kafka_brokers, CHAT_EXECUTION_UPDATES_TOPIC)
    application.state.chat_tool_http = httpx.AsyncClient(timeout=ENVELOPE_HTTP_TIMEOUT_S)
    application.state.chat_tool_executor = HttpChatToolExecutor(
        application.state.chat_tool_http, settings.tracer_api_url, settings.agent_api_url
    )
    temporal_client = TemporalClientProvider(settings.connect_temporal)
    application.state.execution_dispatch = TemporalExecutionDispatch(temporal_client)
    application.state.job_dispatch = TemporalJobDispatch(temporal_client)
    application.state.job_envelope_http = httpx.AsyncClient(timeout=ENVELOPE_HTTP_TIMEOUT_S)
    application.state.job_envelopes = JobEnvelopeClient(
        application.state.job_envelope_http, settings.agent_api_url
    )
    application.state.rule_anchor_http = httpx.AsyncClient(timeout=ENVELOPE_HTTP_TIMEOUT_S)
    application.state.rule_anchors = RuleAnchorClient(
        application.state.rule_anchor_http, settings.tracer_api_url
    )
    try:
        yield
    finally:
        shutdown_observability()
        await application.state.rule_anchor_http.aclose()
        await application.state.job_envelope_http.aclose()
        await application.state.chat_tool_http.aclose()
        await application.state.execution_watch.close()
        await application.state.execution_updates.close()
        await application.state.executions.close()


async def health() -> dict[str, str]:
    return {"status": "ok"}


async def readiness(request: Request) -> JSONResponse:
    """의존에 닿아 요청을 처리할 수 있는지 신원 없이 알리며 봉투를 씌우지 않는다."""
    source: SqlSource = request.app.state.execution_sql
    try:
        async with source.connect() as sql:
            await sql.fetch(READINESS_PROBE)
    except Exception:
        return JSONResponse(status_code=UNREADY_STATUS, content={"status": "unready"})
    return JSONResponse(status_code=200, content={"status": "ok"})


def create_app() -> FastAPI:
    """독립 수명을 가진 에이전트 HTTP 앱을 만든다."""
    application = FastAPI(title="tracer-agent", lifespan=lifespan)
    application.get("/health")(health)
    application.get("/health/ready")(readiness)
    application.get(SURFACE_PATH)(get_served_surface)
    # 브라우저가 백엔드마다 다른 경로를 치지 않도록 계약이 정한 경로로 연다.
    application.get(CHAT_THREADS_PATH)(list_chat_threads)
    application.post(CHAT_THREADS_PATH, status_code=CHAT_CREATED_STATUS)(create_chat_thread)
    application.get(CHAT_THREAD_PATH)(get_chat_thread)
    application.patch(CHAT_THREAD_PATH)(rename_chat_thread)
    application.delete(CHAT_THREAD_PATH)(delete_chat_thread)
    application.get(CHAT_THREAD_MESSAGES_PATH)(list_chat_messages)
    application.post(CHAT_MESSAGES_PATH, status_code=ACCEPTED_STATUS)(enqueue_chat_turn)
    application.post(CHAT_CONFIRMATIONS_PATH, status_code=CHAT_CREATED_STATUS)(propose_chat_tool)
    application.post(CHAT_CONFIRMATION_PATH)(decide_chat_tool)
    application.get(CHAT_EXECUTIONS_PATH)(list_chat_executions)
    application.get(CHAT_EXECUTION_EVENTS_PATH, response_model=None)(watch_chat_execution)
    application.get(CHAT_EXECUTION_STEPS_PATH)(list_chat_execution_steps)
    application.get(CHAT_EXECUTION_REPLAY_PATH)(get_chat_replay)
    application.post(CHAT_CANCEL_PATH)(cancel_chat_turn)
    application.post(CHAT_DRAFTS_PATH)(checkpoint_chat_draft)
    application.get(CHAT_MEMORIES_PATH)(recall_chat_facts)
    application.put(CHAT_MEMORY_PATH)(remember_chat_fact)
    application.include_router(job_query_router)
    application.include_router(job_intake_router)
    application.include_router(settings_router)
    application.include_router(envelope_router)
    return application


app = create_app()
