"""chat과 jobs와 generate 중 기동 인자로 받은 큐 하나만 폴링하는 Temporal 워커 프로세스를 세운다."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from temporalio.client import Client
from temporalio.worker import Worker

from ..shared.agents.runtime.ledger import LedgerPoolProvider, PooledSql
from ..shared.agents.runtime.notification import JobStatusNotifier
from ..shared.agents.runtime.telemetry.bootstrap import configure_observability
from ..shared.agents.runtime.wakeup import UpdatePublisher
from ..shared.agents.shared.job_kinds import JOB_AGENT_NAMES
from ..shared.config import Settings, get_settings
from ..shared.workflows.chat_spec import (
    CHAT_EXECUTION_UPDATES_TOPIC,
    CHAT_QUEUE_KEY,
    CHAT_TASK_QUEUE,
)
from ..shared.workflows.dispatch import TemporalClientProvider, TemporalExecutionDispatch
from ..shared.workflows.jobs_envelope import JobEnvelopeClient
from ..shared.workflows.jobs_spec import (
    GENERATE_QUEUE_KEY,
    GENERATE_TASK_QUEUE,
    JOB_UPDATED_NOTIFICATION,
    JOBS_QUEUE_KEY,
    JOBS_TASK_QUEUE,
    NOTIFICATIONS_TOPIC,
)
from .agents.runtime.checkpoint import GraphCheckpointProvider
from .agents.shared.contract_prompt_source import ContractPromptSource
from .sdk_metrics import open_sdk_metrics
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
JobActivity = Callable[..., Awaitable[Any]]


@dataclass
class ChatWorkerResources:
    """chat 액티비티가 쓸 바깥 연결이며 프로세스가 끝날 때 함께 닫는다."""

    ledger: LedgerPoolProvider
    http_client: httpx.AsyncClient
    checkpoints: GraphCheckpointProvider
    wakeup: UpdatePublisher

    async def close(self) -> None:
        """열린 연결을 모두 닫는다."""
        await self.http_client.aclose()
        await self.ledger.close()
        await self.checkpoints.close()
        await self.wakeup.close()


@dataclass
class JobWorkerResources:
    """잡 셋 액티비티가 쓸 바깥 연결이며 프로세스가 끝날 때 함께 닫는다."""

    http_client: httpx.AsyncClient
    execution: LedgerPoolProvider
    notifications: UpdatePublisher
    checkpoints: GraphCheckpointProvider

    async def close(self) -> None:
        """열린 연결을 모두 닫는다."""
        await self.http_client.aclose()
        await self.execution.close()
        await self.notifications.close()
        await self.checkpoints.close()


@asynccontextmanager
async def chat_resources(settings: Settings) -> AsyncIterator[ChatWorkerResources]:
    """chat 액티비티가 쓸 바깥 연결을 열고 끝나면 닫는다."""
    http_client = httpx.AsyncClient(timeout=CHAT_HTTP_TIMEOUT_S)
    opened = ChatWorkerResources(
        ledger=LedgerPoolProvider(
            settings.agent_dsn(),
            min_size=settings.agent_db_pool_min_size,
            max_size=settings.agent_db_pool_max_size,
        ),
        http_client=http_client,
        checkpoints=GraphCheckpointProvider(settings.checkpoint_dsn()),
        wakeup=UpdatePublisher(settings.kafka_brokers, CHAT_EXECUTION_UPDATES_TOPIC),
    )
    try:
        yield opened
    finally:
        await opened.close()


@asynccontextmanager
async def job_resources(settings: Settings) -> AsyncIterator[JobWorkerResources]:
    """잡 셋 액티비티가 쓸 바깥 연결을 열고 끝나면 닫는다."""
    http_client = httpx.AsyncClient(timeout=JOB_HTTP_TIMEOUT_S)
    opened = JobWorkerResources(
        http_client=http_client,
        execution=LedgerPoolProvider(
            settings.agent_dsn(),
            min_size=settings.agent_db_pool_min_size,
            max_size=settings.agent_db_pool_max_size,
        ),
        notifications=UpdatePublisher(settings.kafka_brokers, NOTIFICATIONS_TOPIC),
        checkpoints=GraphCheckpointProvider(settings.checkpoint_dsn()),
    )
    try:
        yield opened
    finally:
        await opened.close()


def build_chat_worker(client: Client, opened: ChatWorkerResources, settings: Settings) -> Worker:
    """chat 큐의 워크플로와 액티비티만 소비하는 워커를 만든다."""
    activities = ChatExecutionActivities(
        PooledSql(opened.ledger, settings.agent_db_acquire_timeout_s),
        opened.http_client,
        opened.checkpoints,
        ChatEnvelopeClient(opened.http_client, settings.agent_api_url),
        ContractPromptSource().resolve("chat"),
        opened.wakeup,
    )
    return Worker(
        client,
        task_queue=CHAT_TASK_QUEUE,
        workflows=[ChatThreadWorkflow, ChatExecutionWorkflow],
        activities=[
            activities.next_execution,
            activities.prepare,
            activities.generate,
            activities.finalize,
            activities.fail,
        ],
        graceful_shutdown_timeout=timedelta(seconds=SHUTDOWN_GRACE_S),
    )


def job_activities(opened: JobWorkerResources, settings: Settings) -> AgentJobActivities:
    """잡 셋의 액티비티를 열린 연결에 이어 낸다."""
    source = ContractPromptSource()
    return AgentJobActivities(
        settings.tracer_api_url,
        opened.http_client,
        PooledSql(opened.execution, settings.agent_db_acquire_timeout_s),
        {name: source.resolve(name) for name in JOB_AGENT_NAMES},
        envelopes=JobEnvelopeClient(opened.http_client, settings.agent_api_url),
        notifier=JobStatusNotifier(opened.notifications, JOB_UPDATED_NOTIFICATION),
        checkpoints=opened.checkpoints,
    )


def short_job_activities(activities: AgentJobActivities) -> list[JobActivity]:
    """모델을 부르지 않는 액티비티만 골라 jobs 큐에 등록한다."""
    return [
        activities.prepare,
        activities.finalize,
        activities.fail,
        activities.settle_canceled,
    ]


def generate_job_activities(activities: AgentJobActivities) -> list[JobActivity]:
    """모델을 부르는 긴 액티비티만 골라 generate 큐에 등록한다."""
    return [activities.generate]


def build_job_worker(client: Client, opened: JobWorkerResources, settings: Settings) -> Worker:
    """잡 셋 워크플로 하나와 짧은 액티비티만 소비하는 워커를 만든다."""
    return Worker(
        client,
        task_queue=JOBS_TASK_QUEUE,
        workflows=[AgentJobWorkflow],
        activities=short_job_activities(job_activities(opened, settings)),
        graceful_shutdown_timeout=timedelta(seconds=SHUTDOWN_GRACE_S),
    )


def build_generate_worker(client: Client, opened: JobWorkerResources, settings: Settings) -> Worker:
    """모델을 부르는 긴 액티비티만 소비하고 워크플로는 소유하지 않는 워커를 만든다."""
    return Worker(
        client,
        task_queue=GENERATE_TASK_QUEUE,
        activities=generate_job_activities(job_activities(opened, settings)),
        graceful_shutdown_timeout=timedelta(seconds=SHUTDOWN_GRACE_S),
    )


async def resume_chat_executions(client: Client, opened: Any, settings: Settings) -> None:
    """워커가 서기 전에 접수만 되고 시그널이 닿지 못한 실행을 다시 워크플로에 얹는다."""
    dispatch = TemporalExecutionDispatch(TemporalClientProvider(_ready(client)))
    source = PooledSql(opened.ledger, settings.agent_db_acquire_timeout_s)
    swept = await resume_active_executions(source, dispatch)
    _log.info("chat.workflow.resumed recovered=%d resumed=%d", swept.recovered, swept.resumed)


async def _nothing_to_resume(_client: Client, _opened: Any, _settings: Settings) -> None:
    """다시 얹을 대기 줄을 갖지 않는 큐다."""


@dataclass(frozen=True)
class WorkerProfile:
    """큐 하나가 여는 자원과 그 자원으로 세우는 워커와 기동 전에 할 일을 한 칸에 담는다."""

    resources: Callable[[Settings], AbstractAsyncContextManager[Any]]
    build: Callable[[Client, Any, Settings], Worker]
    resume: Callable[[Client, Any, Settings], Awaitable[None]] = _nothing_to_resume

    async def serve(self, settings: Settings, client: Client) -> None:
        """이 큐의 자원을 열어 워커를 세우고 종료 신호가 올 때까지 폴링한다."""
        async with self.resources(settings) as opened:
            await self.resume(client, opened, settings)
            await self.build(client, opened, settings).run()


# 큐를 더할 때 이 표에 한 줄을 더하며 기동 분기를 고치지 않는다.
WORKER_PROFILES: dict[WorkerQueue, WorkerProfile] = {
    CHAT_QUEUE_KEY: WorkerProfile(chat_resources, build_chat_worker, resume_chat_executions),
    JOBS_QUEUE_KEY: WorkerProfile(job_resources, build_job_worker),
    GENERATE_QUEUE_KEY: WorkerProfile(job_resources, build_generate_worker),
}
QUEUE_ARGS = tuple(WORKER_PROFILES)


async def serve(queue: WorkerQueue) -> None:
    """Temporal에 붙어 기동 인자로 받은 큐 하나만 폴링하며 종료 신호가 올 때까지 실행한다."""
    settings = get_settings()
    settings.configure_langsmith()
    shutdown_observability = configure_observability()
    # SDK runtime 은 클라이언트보다 먼저 서야 그 클라이언트와 워커의 지표가 같은 창구로 나간다.
    open_sdk_metrics()
    client = await settings.connect_temporal()
    try:
        await WORKER_PROFILES[queue].serve(settings, client)
    finally:
        shutdown_observability()


def _ready(client: Client) -> Callable[[], Awaitable[Client]]:
    async def connected() -> Client:
        return client

    return connected


def _parse_queue(argv: list[str]) -> WorkerQueue:
    """기동 인자 하나로 이 프로세스가 폴링할 큐를 정하며 인자가 없으면 chat 큐를 쓴다."""
    if not argv:
        return CHAT_QUEUE_KEY
    queue = argv[0]
    if queue not in QUEUE_ARGS:
        expected = " or ".join(repr(arg) for arg in QUEUE_ARGS)
        raise SystemExit(f"unknown worker queue argument: {queue!r} (expected {expected})")
    return queue


def main() -> None:
    """워커 프로세스를 큐 인자로 받아 시작한다."""
    asyncio.run(serve(_parse_queue(sys.argv[1:])))
