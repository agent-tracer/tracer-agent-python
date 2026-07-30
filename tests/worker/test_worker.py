"""워커 프로세스가 기동 인자로 받은 큐 하나와 그 큐의 액티비티만 갖는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.fakes import TRACER_API_URL, FakeLedgerPool
from tracer_agent.shared.agents.runtime.ledger import PooledSql
from tracer_agent.worker.worker import (
    QUEUE_ARGS,
    _parse_queue,
    generate_job_activities,
    short_job_activities,
)
from tracer_agent.worker.workflows.jobs_activities import AgentJobActivities


def _activities() -> AgentJobActivities:
    return AgentJobActivities(TRACER_API_URL, None, PooledSql(FakeLedgerPool()))  # type: ignore[arg-type]


def test_기동_인자가_계약의_큐_키_셋이다() -> None:
    assert set(QUEUE_ARGS) == {"chat", "jobs", "generate"}


def test_생성_큐가_모델을_부르는_긴_액티비티만_갖는다() -> None:
    activities = _activities()

    assert generate_job_activities(activities) == [activities.run]


def test_잡_큐가_긴_액티비티를_갖지_않는다() -> None:
    activities = _activities()

    assert short_job_activities(activities) == [activities.settle_canceled]


@pytest.mark.parametrize("queue", QUEUE_ARGS)
def test_기동_인자를_그대로_폴링할_큐로_삼는다(queue: str) -> None:
    assert _parse_queue([queue]) == queue


def test_기동_인자가_없으면_chat으로_물러선다() -> None:
    assert _parse_queue([]) == "chat"


def test_모르는_기동_인자를_거절한다() -> None:
    with pytest.raises(SystemExit):
        _parse_queue(["evaluation"])
