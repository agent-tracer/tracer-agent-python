"""워커 프로세스가 기동 인자로 받은 큐 하나와 그 큐의 액티비티만 갖는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tests.support.fakes import TRACER_API_URL
from tests.support.prompts import JOB_PROMPTS
from tracer_agent.shared.agents.runtime.__fakes__.pool import FakeLedgerPool
from tracer_agent.shared.agents.runtime.ledger import PooledSql
from tracer_agent.shared.config import Settings
from tracer_agent.worker.worker import (
    QUEUE_ARGS,
    WORKER_PROFILES,
    WorkerProfile,
    _parse_queue,
    build_chat_worker,
    build_generate_worker,
    build_job_worker,
    generate_job_activities,
    resume_chat_executions,
    short_job_activities,
)
from tracer_agent.worker.workflows.jobs_activities import AgentJobActivities

SWEEP_TIMEOUT_S = 1.5


class SweepConnection:
    """스윕이 보내는 문장에 빈 결과만 내는 연결 대역이다."""

    async def fetch(self, _sql: str, *_args: Any) -> list[Any]:
        """되돌릴 실행도 다시 얹을 실행도 없다고 낸다."""
        return []


class SweepPool:
    """스윕이 연결을 빌리며 넘긴 여유를 기억하는 풀 대역이다."""

    def __init__(self) -> None:
        self.timeouts: list[float | None] = []

    async def acquire(self, timeout: float | None = None) -> SweepConnection:
        """받은 여유를 적고 빈 결과만 내는 연결을 낸다."""
        self.timeouts.append(timeout)
        return SweepConnection()

    async def release(self, _connection: Any) -> None:
        """반납할 것이 없다."""


class SweepResources:
    """스윕이 쓰는 원장 풀 하나만 실은 워커 자원 대역이다."""

    def __init__(self, pool: SweepPool) -> None:
        self._pool = pool
        self.ledger = self

    async def pool(self) -> SweepPool:
        """세워 둔 풀을 낸다."""
        return self._pool


def _activities() -> AgentJobActivities:
    return AgentJobActivities(TRACER_API_URL, None, PooledSql(FakeLedgerPool()), JOB_PROMPTS)  # type: ignore[arg-type]


def test_기동_인자가_계약의_큐_키_셋이다() -> None:
    assert set(QUEUE_ARGS) == {"chat", "jobs", "generate"}


def test_생성_큐가_모델을_부르는_긴_액티비티만_갖는다() -> None:
    activities = _activities()

    assert generate_job_activities(activities) == [activities.generate]


def test_잡_큐가_긴_액티비티를_갖지_않는다() -> None:
    activities = _activities()

    assert short_job_activities(activities) == [
        activities.prepare,
        activities.finalize,
        activities.fail,
        activities.settle_canceled,
    ]


@pytest.mark.parametrize("queue", QUEUE_ARGS)
def test_기동_인자를_그대로_폴링할_큐로_삼는다(queue: str) -> None:
    assert _parse_queue([queue]) == queue


def test_기동_인자가_없으면_chat으로_물러선다() -> None:
    assert _parse_queue([]) == "chat"


def test_모르는_기동_인자를_거절한다() -> None:
    with pytest.raises(SystemExit):
        _parse_queue(["evaluation"])


class TestWorkerProfiles:
    """큐를 더할 때 기동 분기 대신 표 한 줄만 늘어나는지 고정한다."""

    def test_기동_인자가_프로파일_표에서_그대로_나온다(self) -> None:
        assert tuple(WORKER_PROFILES) == QUEUE_ARGS

    def test_큐마다_자기_자원과_자기_워커를_갖는다(self) -> None:
        builders = {queue: profile.build for queue, profile in WORKER_PROFILES.items()}

        assert builders == {
            "chat": build_chat_worker,
            "jobs": build_job_worker,
            "generate": build_generate_worker,
        }

    async def test_대기_줄_스윕도_배포가_정한_획득_여유로_연결을_빌린다(self) -> None:
        pool = SweepPool()
        settings = Settings(agent_db_acquire_timeout_s=SWEEP_TIMEOUT_S)

        await resume_chat_executions(None, SweepResources(pool), settings)  # type: ignore[arg-type]

        assert pool.timeouts == [SWEEP_TIMEOUT_S]

    def test_대기_줄을_다시_얹는_큐는_chat_하나다(self) -> None:
        resuming = [
            queue for queue, profile in WORKER_PROFILES.items() if profile.resume is resume_chat_executions
        ]

        assert resuming == ["chat"]

    async def test_프로파일이_연_자원으로_워커를_세우고_기동_전에_대기_줄을_얹는다(self) -> None:
        opened: list[str] = []
        resumed: list[str] = []
        served: list[str] = []

        @asynccontextmanager
        async def resources(_settings: Any) -> AsyncIterator[str]:
            opened.append("open")
            yield "opened"
            opened.append("close")

        async def resume(_client: Any, resource: str, _settings: Any) -> None:
            resumed.append(resource)

        def build(_client: Any, resource: str, _settings: Any) -> Any:
            served.append(resource)
            return _RecordingWorker(served)

        await WorkerProfile(resources, build, resume).serve(None, None)  # type: ignore[arg-type]

        assert opened == ["open", "close"]
        assert resumed == ["opened"]
        assert served == ["opened", "ran"]


class _RecordingWorker:
    """워커 대신 폴링이 시작됐다는 사실만 남기는 대역이다."""

    def __init__(self, served: list[str]) -> None:
        self._served = served

    async def run(self) -> None:
        self._served.append("ran")
