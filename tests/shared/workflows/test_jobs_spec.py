"""잡 큐와 워크플로 이름과 긴 생성의 큐 배치가 계약과 같은지 검증한다."""

from __future__ import annotations

from tests.support.contract import workflow_contract
from tracer_agent.shared.config import DEFAULT_TASK_QUEUE_PREFIX
from tracer_agent.shared.workflows.chat_spec import CHAT_TASK_QUEUE
from tracer_agent.shared.workflows.jobs_spec import (
    AGENT_JOB_WORKFLOW,
    FAIL_AGENT_JOB_ACTIVITY,
    FINALIZE_AGENT_JOB_ACTIVITY,
    GENERATE_AGENT_JOB_ACTIVITY,
    GENERATE_QUEUE_KEY,
    GENERATE_TASK_QUEUE,
    JOB_CANCEL_SETTLE_TIMEOUT_S,
    JOB_FINALIZE_MAX_ATTEMPTS,
    JOB_FINALIZE_TIMEOUT_S,
    JOB_GENERATE_MAX_ATTEMPTS,
    JOB_GENERATE_SCHEDULE_TO_CLOSE_S,
    JOB_GENERATE_TIMEOUT_S,
    JOB_HEARTBEAT_INTERVAL_S,
    JOB_HEARTBEAT_TIMEOUT_S,
    JOB_PREPARE_MAX_ATTEMPTS,
    JOB_PREPARE_TIMEOUT_S,
    JOBS_QUEUE_KEY,
    JOBS_TASK_QUEUE,
    PREPARE_AGENT_JOB_ACTIVITY,
    SETTLE_CANCELED_JOB_ACTIVITY,
    agent_job_workflow_id,
)

_CONTRACT = workflow_contract("queues.yaml")
_AGENT_JOB = _CONTRACT["jobWorkflows"]["singleKind"]["agentJob"]
_QUEUE_OF = {activity["name"]: activity["queue"] for activity in _AGENT_JOB["activities"]}


def test_큐_이름을_접두사와_계약의_키가_만든다() -> None:
    assert {JOBS_QUEUE_KEY, GENERATE_QUEUE_KEY} <= set(_CONTRACT["queues"])
    assert JOBS_TASK_QUEUE.split("-", 1) == [DEFAULT_TASK_QUEUE_PREFIX, JOBS_QUEUE_KEY]
    assert GENERATE_TASK_QUEUE.split("-", 1) == [DEFAULT_TASK_QUEUE_PREFIX, GENERATE_QUEUE_KEY]


def test_세_큐가_서로_나뉘어_있다() -> None:
    assert len({JOBS_TASK_QUEUE, GENERATE_TASK_QUEUE, CHAT_TASK_QUEUE}) == 3


def test_워크플로_이름이_계약이_적은_이름과_같다() -> None:
    assert _AGENT_JOB["name"] == AGENT_JOB_WORKFLOW
    assert _AGENT_JOB["queue"] == JOBS_QUEUE_KEY


def test_워크플로_식별자가_계약이_적은_모양을_따른다() -> None:
    template = _AGENT_JOB["id"]

    assert agent_job_workflow_id("recipe-scan", "k1") == template.format(kind="recipe-scan", key="k1")


def test_워크플로_식별자가_잡_종류마다_나뉜다() -> None:
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("recipe-scan", "k1")
    assert agent_job_workflow_id("title-suggestion", "k1") != agent_job_workflow_id("title-suggestion", "k2")


def test_모델을_부르는_생성_액티비티가_generate_큐에서_실행된다() -> None:
    assert _QUEUE_OF[GENERATE_AGENT_JOB_ACTIVITY] == GENERATE_QUEUE_KEY


def test_모델을_부르지_않는_준비_액티비티가_jobs_큐에서_실행된다() -> None:
    assert _QUEUE_OF[PREPARE_AGENT_JOB_ACTIVITY] == JOBS_QUEUE_KEY


def test_취소_닫기_액티비티가_jobs_큐에서_실행된다() -> None:
    assert _QUEUE_OF[SETTLE_CANCELED_JOB_ACTIVITY] == JOBS_QUEUE_KEY


def test_액티비티의_상한과_시도_수가_계약이_적은_값과_같다() -> None:
    activities = {activity["name"]: activity for activity in _AGENT_JOB["activities"]}
    prepare = activities[PREPARE_AGENT_JOB_ACTIVITY]
    generate = activities[GENERATE_AGENT_JOB_ACTIVITY]
    finalize = activities[FINALIZE_AGENT_JOB_ACTIVITY]

    assert prepare["startToCloseSeconds"] == JOB_PREPARE_TIMEOUT_S
    assert prepare["maximumAttempts"] == JOB_PREPARE_MAX_ATTEMPTS
    assert generate["startToCloseSeconds"] == JOB_GENERATE_TIMEOUT_S
    assert generate["scheduleToCloseSeconds"] == JOB_GENERATE_SCHEDULE_TO_CLOSE_S
    assert generate["maximumAttempts"] == JOB_GENERATE_MAX_ATTEMPTS
    assert generate["heartbeatTimeoutSeconds"] == JOB_HEARTBEAT_TIMEOUT_S
    assert generate["heartbeatIntervalSeconds"] == JOB_HEARTBEAT_INTERVAL_S
    assert finalize["startToCloseSeconds"] == JOB_FINALIZE_TIMEOUT_S
    assert finalize["maximumAttempts"] == JOB_FINALIZE_MAX_ATTEMPTS
    assert activities[SETTLE_CANCELED_JOB_ACTIVITY]["startToCloseSeconds"] == JOB_CANCEL_SETTLE_TIMEOUT_S


def test_실패_처리_액티비티가_종결과_같은_상한과_시도_수를_쓴다() -> None:
    fail = {activity["name"]: activity for activity in _AGENT_JOB["activities"]}[FAIL_AGENT_JOB_ACTIVITY]

    assert fail["startToCloseSeconds"] == JOB_FINALIZE_TIMEOUT_S
    assert fail["maximumAttempts"] == JOB_FINALIZE_MAX_ATTEMPTS
