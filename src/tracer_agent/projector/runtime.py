"""배경 작업 셋이 쓸 바깥 연결을 열고 그 연결에 세 작업을 이어 낸다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from ..shared.agents.recipe.consumer import LedgerEventConsumer
from ..shared.agents.recipe.index import SearchIndexAdminPort, recipes_index_definition
from ..shared.agents.recipe.outbox import (
    LedgerSearchOutboxDrain,
    SearchOutboxDrain,
    SearchOutboxDrainScheduler,
)
from ..shared.agents.recipe.search import (
    OpenSearchClient,
    OpenSearchIndexAdmin,
    OpenSearchIndexWriter,
)
from ..shared.agents.runtime.ledger import LedgerPoolProvider, PooledSql
from ..shared.config import Settings

# 색인을 부르는 여유이며 배출 주기가 밀려도 요청 하나가 끝나게 둔다.
OUTBOUND_HTTP_TIMEOUT_S = 20.0


@dataclass
class ProjectorResources:
    """배경 작업이 쓸 바깥 연결이며 프로세스가 끝날 때 함께 닫는다."""

    ledger: LedgerPoolProvider
    search_http: httpx.AsyncClient
    execution_sql: PooledSql

    async def close(self) -> None:
        """열린 연결을 모두 닫는다."""
        await self.search_http.aclose()
        await self.ledger.close()


@dataclass(frozen=True)
class ProjectorJobs:
    """이 배포 단위가 맡는 배경 작업 셋이며 함께 시작하고 함께 멈춘다."""

    index_admin: SearchIndexAdminPort
    drain: SearchOutboxDrainScheduler
    ledger_events: LedgerEventConsumer

    async def start(self) -> None:
        """색인을 먼저 세우고 배출과 투영을 시작한다."""
        await self.index_admin.ensure_index(recipes_index_definition())
        self.drain.start()
        await self.ledger_events.start()

    async def stop(self) -> None:
        """투영과 배출을 멈춘다."""
        await self.ledger_events.stop()
        await self.drain.stop()


@asynccontextmanager
async def projector_resources(settings: Settings) -> AsyncIterator[ProjectorResources]:
    """배경 작업이 쓸 바깥 연결을 열고 끝나면 닫는다."""
    ledger = LedgerPoolProvider(
        settings.agent_dsn(),
        min_size=settings.agent_db_pool_min_size,
        max_size=settings.agent_db_pool_max_size,
    )
    opened = ProjectorResources(
        ledger=ledger,
        search_http=httpx.AsyncClient(timeout=OUTBOUND_HTTP_TIMEOUT_S),
        execution_sql=PooledSql(ledger, settings.agent_db_acquire_timeout_s),
    )
    try:
        yield opened
    finally:
        await opened.close()


def build_projector(settings: Settings, opened: ProjectorResources) -> ProjectorJobs:
    """열린 연결에 색인 세우기와 배출과 투영을 이어 낸다."""
    search_client = OpenSearchClient(opened.search_http, settings.opensearch_url)
    return ProjectorJobs(
        index_admin=OpenSearchIndexAdmin(search_client),
        drain=SearchOutboxDrainScheduler(
            SearchOutboxDrain(
                LedgerSearchOutboxDrain(opened.execution_sql), OpenSearchIndexWriter(search_client)
            )
        ),
        ledger_events=LedgerEventConsumer(settings.kafka_brokers, opened.execution_sql),
    )
