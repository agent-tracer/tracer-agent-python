"""잡과 대화의 접수와 조회와 취소를, 그리고 배포 단위 사이의 내부 창구를 계약이 정한 표면으로 노출한다."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..shared.agents.chat.intake.router import router as chat_intake_router
from ..shared.agents.chat.surface.confirmations import router as chat_confirmation_router
from ..shared.agents.chat.surface.drafts import router as chat_draft_router
from ..shared.agents.chat.surface.executions import router as chat_execution_router
from ..shared.agents.chat.surface.memories import router as chat_memory_router
from ..shared.agents.chat.surface.stream import router as chat_stream_router
from ..shared.agents.chat.surface.threads import router as chat_thread_router
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
from ..shared.workflows.jobs_anchor import ScanAnchorClient
from ..shared.workflows.jobs_dispatch import TemporalJobDispatch
from ..shared.workflows.jobs_intake import router as job_intake_router
from ..shared.workflows.jobs_query import router as job_query_router
from .credentials import SettingModelCredentials
from .services import AgentServices
from .surface import SURFACE_PATH, get_served_surface

# 배포 단위 사이의 창구를 부를 때 쓰는 여유이며 실행 자체를 기다리지 않는다.
OUTBOUND_HTTP_TIMEOUT_S = 20.0

# 원장까지 왕복하는 질의라야 연결이 살아 있다는 것을 알린다.
READINESS_PROBE = "SELECT 1"
UNREADY_STATUS = 503


_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.configure_langsmith()
    shutdown_observability = configure_observability()
    executions = LedgerPoolProvider(settings.agent_dsn())
    execution_sql = PooledSql(executions, settings.agent_db_acquire_timeout_s)
    encryption_key = settings.monitor_settings_encryption_key
    cipher = SettingCipher(
        None if encryption_key is None else encryption_key.get_secret_value(),
        settings.monitor_profile,
    )
    updates = UpdatePublisher(settings.kafka_brokers, CHAT_EXECUTION_UPDATES_TOPIC)
    watch = UpdateSubscriber(settings.kafka_brokers, CHAT_EXECUTION_UPDATES_TOPIC)
    chat_tool_http = httpx.AsyncClient(timeout=OUTBOUND_HTTP_TIMEOUT_S)
    anchor_http = httpx.AsyncClient(timeout=OUTBOUND_HTTP_TIMEOUT_S)
    temporal_client = TemporalClientProvider(settings.connect_temporal)
    application.state.services = AgentServices(
        execution_sql=execution_sql,
        setting_cipher=cipher,
        model_credentials=SettingModelCredentials(execution_sql, cipher),
        chat_tool_executor=HttpChatToolExecutor(
            chat_tool_http, settings.tracer_api_url, settings.agent_api_url
        ),
        execution_dispatch=TemporalExecutionDispatch(temporal_client),
        job_dispatch=TemporalJobDispatch(temporal_client),
        scan_anchors=ScanAnchorClient(anchor_http, settings.tracer_api_url),
        read_api_base_url=settings.tracer_api_url,
        agent_api_base_url=settings.agent_api_url,
        execution_updates=updates,
        execution_watch=watch,
    )
    try:
        yield
    finally:
        shutdown_observability()
        await anchor_http.aclose()
        await chat_tool_http.aclose()
        await watch.close()
        await updates.close()
        await executions.close()


async def health() -> dict[str, str]:
    return {"status": "ok"}


async def readiness(request: Request) -> JSONResponse:
    """의존에 닿아 요청을 처리할 수 있는지 신원 없이 알리며 봉투를 씌우지 않는다."""
    source: SqlSource = request.app.state.services.execution_sql
    try:
        async with source.connect() as sql:
            await sql.fetch(READINESS_PROBE)
    except Exception:
        # 사유를 감추는 것은 응답이지 로그가 아니다.
        _log.warning("agent api readiness probe failed", exc_info=True)
        return JSONResponse(status_code=UNREADY_STATUS, content={"status": "unready"})
    return JSONResponse(status_code=200, content={"status": "ok"})


def create_app() -> FastAPI:
    """독립 수명을 가진 에이전트 HTTP 앱을 만든다."""
    application = FastAPI(title="tracer-agent", lifespan=lifespan)
    application.get("/health")(health)
    application.get("/health/ready")(readiness)
    application.get(SURFACE_PATH)(get_served_surface)
    # 브라우저가 백엔드마다 다른 경로를 치지 않도록 계약이 정한 경로로 연다.
    application.include_router(chat_intake_router)
    application.include_router(chat_stream_router)
    application.include_router(chat_thread_router)
    application.include_router(chat_confirmation_router)
    application.include_router(chat_execution_router)
    application.include_router(chat_draft_router)
    application.include_router(chat_memory_router)
    application.include_router(job_query_router)
    application.include_router(job_intake_router)
    application.include_router(settings_router)
    application.include_router(envelope_router)
    return application


app = create_app()
