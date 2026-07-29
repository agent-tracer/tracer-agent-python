"""chat 실행이나 잡 셋 중 기동 인자로 받은 큐 하나만 폴링하는 Temporal 워커 프로세스를 세운다."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import httpx
from opensearchpy import AsyncOpenSearch
from temporalio.client import Client
from temporalio.worker import Worker

from ..shared.agents.runtime.ledger import LedgerPoolProvider, PooledSql
from ..shared.agents.runtime.telemetry.bootstrap import configure_observability
from ..shared.agents.runtime.wakeup import UpdatePublisher
from ..shared.config import Settings, get_settings
from ..shared.workflows.chat_spec import CHAT_EXECUTION_UPDATES_TOPIC, CHAT_TASK_QUEUE
from ..shared.workflows.dispatch import TemporalClientProvider, TemporalExecutionDispatch
from ..shared.workflows.jobs_envelope import JobEnvelopeClient
from ..shared.workflows.jobs_spec import GRAPH_JOB_QUEUE
from .agents.chat.checkpoint import ChatCheckpointProvider
from .agents.runtime.search import create_search_client
from .prompt_registry.bootstrap import resolve_fragments_or_fallback
from .prompt_registry.check import assert_prompt_registry_synced_at
from .workflows.chat_activities import ChatExecutionActivities
from .workflows.chat_workflows import ChatExecutionWorkflow, ChatThreadWorkflow
from .workflows.envelope import ChatEnvelopeClient
from .workflows.jobs_activities import AgentJobActivities
from .workflows.jobs_workflows import AgentJobWorkflow
from .workflows.recovery import resume_active_executions

_log = logging.getLogger(__name__)

# 도구 호출과 재생 조회가 한 턴 안에서 여러 번 나가므로 접수 창구보다 넉넉히 잡는다.
CHAT_HTTP_TIMEOUT_S = 60.0
# 완료 창구 배달만 하므로 접수가 쓰던 것과 같은 여유를 둔다.
JOB_HTTP_TIMEOUT_S = 30.0
# 진행 중인 턴을 끊지 않고 마치도록 모델 호출 상한만큼 종료를 기다린다.
SHUTDOWN_GRACE_S = 15 * 60.0

WorkerQueue = str
CHAT_QUEUE_ARG = "chat"
JOBS_QUEUE_ARG = "jobs"


@dataclass
class ChatWorkerResources:
    """chat 액티비티가 쓸 바깥 연결이며 프로세스가 끝날 때 함께 닫는다."""

    ledger: LedgerPoolProvider
    http_client: httpx.AsyncClient
    checkpoints: ChatCheckpointProvider
    wakeup: UpdatePublisher
    prompt_fragments: Mapping[tuple[str, str], Mapping[str, object]] | None

    async def close(self) -> None:
        """열린 연결을 모두 닫는다."""
        await self.http_client.aclose()
        await self.ledger.close()
        await self.checkpoints.close()
        await self.wakeup.close()


@dataclass
class JobWorkerResources:
    """잡 셋 액티비티가 쓸 바깥 연결이며 프로세스가 끝날 때 함께 닫는다."""

    ledger: LedgerPoolProvider
    search: AsyncOpenSearch
    http_client: httpx.AsyncClient
    execution: LedgerPoolProvider
    prompt_fragments: Mapping[tuple[str, str], Mapping[str, object]] | None

    async def close(self) -> None:
        """열린 연결을 모두 닫는다."""
        await self.http_client.aclose()
        await self.ledger.close()
        await self.search.close()
        await self.execution.close()


@asynccontextmanager
async def chat_resources(settings: Settings) -> AsyncIterator[ChatWorkerResources]:
    """chat 액티비티가 쓸 바깥 연결을 열고 끝나면 닫는다."""
    http_client = httpx.AsyncClient(timeout=CHAT_HTTP_TIMEOUT_S)
    try:
        # 코드 pin이 DB production 채널과 같은지만 읽어서 검사하며, 실행은 이 값을 쓰지 않는다.
        await assert_prompt_registry_synced_at(settings.tracer_dsn())
        prompt_fragments = await resolve_fragments_or_fallback(
            http_client, settings.tracer_api_url, settings.monitor_profile
        )
    except BaseException:
        await http_client.aclose()
        raise
    opened = ChatWorkerResources(
        ledger=LedgerPoolProvider(settings.execution_dsn()),
        http_client=http_client,
        checkpoints=ChatCheckpointProvider(settings.checkpoint_dsn()),
        wakeup=UpdatePublisher(settings.kafka_brokers, CHAT_EXECUTION_UPDATES_TOPIC),
        prompt_fragments=prompt_fragments,
    )
    try:
        yield opened
    finally:
        await opened.close()


@asynccontextmanager
async def job_resources(settings: Settings) -> AsyncIterator[JobWorkerResources]:
    """잡 셋 액티비티가 쓸 바깥 연결을 열고 끝나면 닫는다."""
    http_client = httpx.AsyncClient(timeout=JOB_HTTP_TIMEOUT_S)
    try:
        # 코드 pin이 DB production 채널과 같은지만 읽어서 검사하며, 실행은 이 값을 쓰지 않는다.
        await assert_prompt_registry_synced_at(settings.tracer_dsn())
        prompt_fragments = await resolve_fragments_or_fallback(
            http_client, settings.tracer_api_url, settings.monitor_profile
        )
    except BaseException:
        await http_client.aclose()
        raise
    opened = JobWorkerResources(
        ledger=LedgerPoolProvider(settings.tracer_dsn()),
        search=create_search_client(settings.opensearch_node),
        http_client=http_client,
        # 잡 원장·관측은 자기 실행에만 쓰기가 열린 별도 역할로 적는다.
        execution=LedgerPoolProvider(settings.execution_dsn()),
        prompt_fragments=prompt_fragments,
    )
    try:
        yield opened
    finally:
        await opened.close()


def build_chat_worker(client: Client, opened: ChatWorkerResources, settings: Settings) -> Worker:
    """chat 워크플로 둘과 액티비티 넷만 소비하는 워커를 만든다."""
    activities = ChatExecutionActivities(
        PooledSql(opened.ledger),
        opened.http_client,
        opened.checkpoints,
        ChatEnvelopeClient(opened.http_client, settings.tracer_api_url),
        opened.wakeup,
        opened.prompt_fragments,
    )
    return Worker(
        client,
        task_queue=CHAT_TASK_QUEUE,
        workflows=[ChatThreadWorkflow, ChatExecutionWorkflow],
        activities=[activities.prepare, activities.generate, activities.finalize, activities.fail],
        graceful_shutdown_timeout=timedelta(seconds=SHUTDOWN_GRACE_S),
    )


def build_job_worker(client: Client, opened: JobWorkerResources, settings: Settings) -> Worker:
    """잡 셋 워크플로 하나와 액티비티 하나만 소비하는 워커를 만든다."""
    activities = AgentJobActivities(
        opened.ledger,
        opened.search,
        opened.http_client,
        PooledSql(opened.execution),
        opened.prompt_fragments,
        JobEnvelopeClient(opened.http_client, settings.tracer_api_url),
    )
    return Worker(
        client,
        task_queue=GRAPH_JOB_QUEUE,
        workflows=[AgentJobWorkflow],
        activities=[activities.run, activities.settle_canceled],
        graceful_shutdown_timeout=timedelta(seconds=SHUTDOWN_GRACE_S),
    )


async def _serve_chat(settings: Settings, client: Client) -> None:
    async with chat_resources(settings) as opened:
        dispatch = TemporalExecutionDispatch(TemporalClientProvider(_ready(client)))
        swept = await resume_active_executions(PooledSql(opened.ledger), dispatch)
        _log.info("chat.workflow.resumed recovered=%d resumed=%d", swept.recovered, swept.resumed)
        await build_chat_worker(client, opened, settings).run()


async def _serve_jobs(settings: Settings, client: Client) -> None:
    async with job_resources(settings) as opened:
        await build_job_worker(client, opened, settings).run()


async def serve(queue: WorkerQueue) -> None:
    """Temporal에 붙어 기동 인자로 받은 큐 하나만 폴링하며 종료 신호가 올 때까지 돈다."""
    settings = get_settings()
    settings.configure_langsmith()
    shutdown_observability = configure_observability()
    client = await settings.connect_temporal()
    try:
        if queue == JOBS_QUEUE_ARG:
            await _serve_jobs(settings, client)
        else:
            await _serve_chat(settings, client)
    finally:
        shutdown_observability()


def _ready(client: Client) -> Callable[[], Awaitable[Client]]:
    async def connected() -> Client:
        return client

    return connected


def _parse_queue(argv: list[str]) -> WorkerQueue:
    """기동 인자 하나로 이 프로세스가 폴링할 큐를 정하며 없으면 chat으로 물러선다."""
    if not argv:
        return CHAT_QUEUE_ARG
    queue = argv[0]
    if queue not in (CHAT_QUEUE_ARG, JOBS_QUEUE_ARG):
        expected = f"{CHAT_QUEUE_ARG!r} or {JOBS_QUEUE_ARG!r}"
        raise SystemExit(f"unknown worker queue argument: {queue!r} (expected {expected})")
    return queue


def main() -> None:
    """워커 프로세스를 큐 인자로 받아 띄운다."""
    asyncio.run(serve(_parse_queue(sys.argv[1:])))
