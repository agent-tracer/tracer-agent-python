"""배경 작업 셋을 프로세스 수명에 걸고 배포가 물을 프로브 둘만 연다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..shared.agents.runtime.ledger import SqlSource
from ..shared.agents.runtime.readiness import UNREADY_STATUS, ledger_ready
from ..shared.agents.runtime.telemetry.bootstrap import configure_observability
from ..shared.config import get_settings
from .runtime import build_projector, projector_resources

# 준비 프로브가 실패를 알리는 이름이며 로그가 어느 배포 단위의 것인지 밝힌다.
DEPLOYMENT_UNIT = "agent-projector"

# 프로브가 원장을 빌리는 자리이며 이 프로세스는 다른 협력자를 창구에 두지 않는다.
LEDGER_ATTRIBUTE = "execution_sql"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    shutdown_observability = configure_observability()
    async with projector_resources(settings) as opened:
        setattr(application.state, LEDGER_ATTRIBUTE, opened.execution_sql)
        jobs = build_projector(settings, opened)
        await jobs.start()
        try:
            yield
        finally:
            await jobs.stop()
            shutdown_observability()


async def health() -> dict[str, str]:
    return {"status": "ok"}


async def readiness(request: Request) -> JSONResponse:
    """원장에 닿아 투영을 반영할 수 있는지 신원 없이 알린다."""
    source: SqlSource = getattr(request.app.state, LEDGER_ATTRIBUTE)
    if not await ledger_ready(source, DEPLOYMENT_UNIT):
        return JSONResponse(status_code=UNREADY_STATUS, content={"status": "unready"})
    return JSONResponse(status_code=200, content={"status": "ok"})


def create_app() -> FastAPI:
    """배경 작업만 맡는 배포 단위의 앱을 만들며 계약의 창구는 열지 않는다."""
    application = FastAPI(title="agent-projector", lifespan=lifespan)
    application.get("/health")(health)
    application.get("/health/ready")(readiness)
    return application


app = create_app()
