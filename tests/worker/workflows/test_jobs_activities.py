"""잡 액티비티가 요청 종류에 맞는 그래프를 실제로 돌리고 완료 창구로 배달하는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tests.support.fakes import (
    TRACER_API_URL,
    WIRE_LIMITS,
    WIRE_MODEL_RATES,
    FakeLedgerPool,
    FakeToolLoopChat,
)
from tests.support.prompts import JOB_PROMPTS
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql, PooledSql
from tracer_agent.shared.workflows.jobs_envelope import JobExecutionEnvelope
from tracer_agent.shared.workflows.jobs_kinds import AgentJobKind
from tracer_agent.shared.workflows.jobs_ledger import JobLedger
from tracer_agent.shared.workflows.jobs_spec import AgentJobRequest
from tracer_agent.worker.agents.recipe_scan import agent as recipe_mod
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.task_cleanup import agent as cleanup_mod
from tracer_agent.worker.agents.title_suggestion import agent as title_mod
from tracer_agent.worker.workflows.jobs_activities import AgentJobActivities, merge_envelope

_COMPLETION_CALLBACK = {"url": "http://worker:8810/runs/complete", "token": "done-1"}
NOW = datetime(2026, 7, 28, tzinfo=UTC)

_TITLE_CONTEXT: dict[str, object] = {
    "title": "Untitled",
    "status": "completed",
    "workspacePath": None,
    "totalEventCount": 0,
    "totalTurnCount": 0,
    "truncated": False,
    "turns": [],
}


class CapturingCompletionClient:
    """완료 창구로 배달된 결과를 그대로 붙잡아 두는 httpx 대역이다."""

    def __init__(self) -> None:
        self.deliveries: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        """배달된 본문을 기억하고 성공 응답을 그대로 낸다."""
        self.deliveries.append({"url": url, **json})
        return httpx.Response(200, request=httpx.Request("POST", url))


class _StaticSql:
    """테스트 하나가 쓰는 메모리 원장을 액티비티에 그대로 빌려 준다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        return self._lend()

    @asynccontextmanager
    async def _lend(self) -> AsyncIterator[LedgerSql]:
        yield self._store


class FakeEnvelopeSource:
    """페이로드에 자격이 없을 때만 불려야 할 봉투 창구 대역이다."""

    def __init__(self) -> None:
        self.issued_for: list[str] = []

    async def issue(self, kind: str, user_id: str) -> JobExecutionEnvelope:
        self.issued_for.append(f"{kind}:{user_id}")
        return JobExecutionEnvelope(
            model="claude-haiku-4-5",
            fallback_model=None,
            api_key="sk-pulled",
            model_rates=WIRE_MODEL_RATES,
            limits=WIRE_LIMITS,
            deadline_ms=300_000,
        )


async def claim(store: SqliteLedgerSql, job_id: str) -> None:
    """액티비티가 전진시킬 대기 행 하나를 세운다."""
    await JobLedger(store).claim(job_id, "user-1", "title.suggestion", "temporal", None, None, None, {}, NOW)


@pytest.fixture
def http() -> CapturingCompletionClient:
    return CapturingCompletionClient()


async def test_title_suggestion_요청을_돌려_완료_창구로_배달한다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        title_mod, "make_chat_pair", lambda *_a, **_k: ChatPair(FakeToolLoopChat([{"suggestions": []}]), None)
    )
    activities = AgentJobActivities(TRACER_API_URL, http, PooledSql(FakeLedgerPool()), JOB_PROMPTS)  # type: ignore[arg-type]
    payload = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "task-1",
        "userId": "user-1",
        "language": "ko",
        "context": _TITLE_CONTEXT,
        "completionCallback": _COMPLETION_CALLBACK,
    }

    await activities.run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, payload))

    assert http.deliveries[0]["token"] == "done-1"
    assert http.deliveries[0]["response"]["data"] == {"suggestions": []}


async def test_task_cleanup_요청을_돌려_완료_창구로_배달한다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cleanup_mod,
        "make_chat_pair",
        lambda *_a, **_k: ChatPair(FakeToolLoopChat([{"suggestions": []}]), None),
    )
    activities = AgentJobActivities(TRACER_API_URL, http, PooledSql(FakeLedgerPool()), JOB_PROMPTS)  # type: ignore[arg-type]
    payload = {
        "model": "claude-sonnet-4-6",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "scannedAt": "2026-07-28T00:00:00Z",
        "userId": "user-1",
        "language": "ko",
        "maxSuggestions": 5,
        "batch": {"candidates": [], "batchTruncated": False},
        "completionCallback": _COMPLETION_CALLBACK,
    }

    await activities.run(AgentJobRequest(AgentJobKind.TASK_CLEANUP, payload))

    assert http.deliveries[0]["response"]["data"] == {"suggestions": []}


async def test_recipe_scan_요청을_돌려_완료_창구로_배달한다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        recipe_mod, "make_chat_pair", lambda *_a, **_k: ChatPair(FakeToolLoopChat([{"recipes": []}]), None)
    )
    activities = AgentJobActivities(TRACER_API_URL, http, PooledSql(FakeLedgerPool()), JOB_PROMPTS)  # type: ignore[arg-type]
    payload = {
        "model": "claude-sonnet-4-6",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "t1",
        "userId": "user-1",
        "language": "ko",
        "completionCallback": _COMPLETION_CALLBACK,
    }

    await activities.run(AgentJobRequest(AgentJobKind.RECIPE_SCAN, payload))

    assert http.deliveries[0]["response"]["data"]["recipes"] == []


async def test_실행_식별자가_있으면_원장에_종료_상태와_비용과_관측이_남는다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        title_mod, "make_chat_pair", lambda *_a, **_k: ChatPair(FakeToolLoopChat([{"suggestions": []}]), None)
    )
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e1")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS
    )
    payload = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "task-1",
        "userId": "user-1",
        "language": "ko",
        "context": _TITLE_CONTEXT,
        "completionCallback": _COMPLETION_CALLBACK,
        "executionId": "e1",
        "attemptId": "1",
    }

    await activities.run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, payload))

    row = execution_sql.rows("ai_jobs")[0]
    assert row["status"] == "completed"
    assert row["usage"]["costUsd"] is not None
    assert row["result"] == {"suggestions": []}
    assert execution_sql.rows("ai_job_steps")[0]["attempt"] == 1
    assert execution_sql.rows("agent_run_observations")[0]["execution_id"] == "e1"
    execution_sql.close()


async def test_페이로드에_자격이_없으면_실행_식별자로_봉투를_당겨온다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        title_mod, "make_chat_pair", lambda *_a, **_k: ChatPair(FakeToolLoopChat([{"suggestions": []}]), None)
    )
    envelopes = FakeEnvelopeSource()
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e2")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS, envelopes=envelopes
    )
    payload = {
        "model": "claude-haiku-4-5",
        "taskId": "task-1",
        "userId": "user-1",
        "language": "ko",
        "context": _TITLE_CONTEXT,
        "completionCallback": _COMPLETION_CALLBACK,
        "executionId": "e2",
    }

    await activities.run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, payload))

    assert envelopes.issued_for == ["title.suggestion:user-1"]
    assert http.deliveries[0]["response"]["data"] == {"suggestions": []}
    execution_sql.close()


def test_봉투가_데드라인과_자격과_한도를_실행_입력에_싣는다() -> None:
    envelope = JobExecutionEnvelope(
        model="claude-haiku-4-5",
        fallback_model=None,
        api_key="sk-pulled",
        model_rates=WIRE_MODEL_RATES,
        limits=WIRE_LIMITS,
        deadline_ms=300_000,
    )

    merged = merge_envelope({"taskId": "task-1", "userId": "user-1"}, envelope)

    assert merged["deadlineMs"] == 300_000
    assert merged["apiKey"] == "sk-pulled"
    assert merged["limits"] == WIRE_LIMITS
    assert merged["model"] == "claude-haiku-4-5"


def test_실행_입력이_고른_모델은_봉투의_기본값보다_우선한다() -> None:
    envelope = JobExecutionEnvelope(
        model="claude-haiku-4-5",
        fallback_model=None,
        api_key="sk-pulled",
        model_rates=WIRE_MODEL_RATES,
        limits=WIRE_LIMITS,
        deadline_ms=300_000,
    )

    merged = merge_envelope({"model": "claude-sonnet-4-6"}, envelope)

    assert merged["model"] == "claude-sonnet-4-6"
    assert merged["deadlineMs"] == 300_000


async def test_페이로드에_자격도_실행_식별자도_없으면_거부한다(http: CapturingCompletionClient) -> None:
    activities = AgentJobActivities(TRACER_API_URL, http, PooledSql(FakeLedgerPool()), JOB_PROMPTS)  # type: ignore[arg-type]
    payload = {"model": "claude-haiku-4-5", "taskId": "task-1"}

    with pytest.raises(ValueError):
        await activities.run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, payload))


async def test_실행_액티비티가_돌기_전에_취소되면_원장이_취소로_닫힌다(
    http: CapturingCompletionClient,
) -> None:
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e3")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS
    )

    await activities.settle_canceled("e3")

    row = execution_sql.rows("ai_jobs")[0]
    assert row["status"] == "canceled"
    execution_sql.close()


async def test_그래프를_돌리기_전에_죽으면_원장이_failed로_닫힌다(http: CapturingCompletionClient) -> None:
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e5")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS
    )
    payload = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "missing-task",
        "userId": "user-1",
        "language": "ko",
        "completionCallback": _COMPLETION_CALLBACK,
        "executionId": "e5",
    }

    with pytest.raises(Exception):  # noqa: B017
        await activities.run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, payload))

    row = execution_sql.rows("ai_jobs")[0]
    assert row["status"] == "failed"
    execution_sql.close()


async def test_이미_종결된_행은_취소_닫기가_건드리지_않는다(http: CapturingCompletionClient) -> None:
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e4")
    await JobLedger(execution_sql).settle("e4", "completed", {}, {}, None, NOW)
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS
    )

    await activities.settle_canceled("e4")

    row = execution_sql.rows("ai_jobs")[0]
    assert row["status"] == "completed"
    execution_sql.close()


class CapturingNotifier:
    """알림 토픽으로 나간 잡 상태 전이를 순서대로 붙잡아 두는 대역이다."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def job_updated(self, user_id: str, payload: dict[str, Any]) -> bool:
        self.published.append((user_id, payload))
        return True


async def test_잡이_돌면_실행과_종결이_상태_전이로_알려진다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        title_mod, "make_chat_pair", lambda *_a, **_k: ChatPair(FakeToolLoopChat([{"suggestions": []}]), None)
    )
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e6")
    notifier = CapturingNotifier()
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS, None, notifier
    )
    payload = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "task-1",
        "userId": "user-1",
        "language": "ko",
        "context": _TITLE_CONTEXT,
        "completionCallback": _COMPLETION_CALLBACK,
        "executionId": "e6",
        "attemptId": "1",
    }

    await activities.run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, payload))

    assert [entry[1]["status"] for entry in notifier.published] == ["running", "completed"]
    assert notifier.published[0][0] == "user-1"
    assert notifier.published[1][1] == {
        "jobId": "e6",
        "kind": "title.suggestion",
        "status": "completed",
        "taskId": "task-1",
    }
    execution_sql.close()


async def test_태스크에_매이지_않은_잡은_태스크_식별자를_싣지_않는다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cleanup_mod,
        "make_chat_pair",
        lambda *_a, **_k: ChatPair(FakeToolLoopChat([{"suggestions": []}]), None),
    )
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e7")
    notifier = CapturingNotifier()
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS, None, notifier
    )
    payload = {
        "model": "claude-sonnet-4-6",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "scannedAt": "2026-07-28T00:00:00Z",
        "userId": "user-1",
        "language": "ko",
        "maxSuggestions": 5,
        "batch": {"candidates": [], "batchTruncated": False},
        "completionCallback": _COMPLETION_CALLBACK,
        "executionId": "e7",
    }

    await activities.run(AgentJobRequest(AgentJobKind.TASK_CLEANUP, payload))

    assert "taskId" not in notifier.published[-1][1]
    assert notifier.published[-1][1]["kind"] == "task.cleanup"
    execution_sql.close()


async def test_그래프를_돌리기_전에_죽으면_실패가_상태_전이로_알려진다(
    http: CapturingCompletionClient,
) -> None:
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e8")
    notifier = CapturingNotifier()
    activities = AgentJobActivities(  # type: ignore[arg-type]
        TRACER_API_URL, http, _StaticSql(execution_sql), JOB_PROMPTS, None, notifier
    )
    payload = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "taskId": "missing-task",
        "userId": "user-1",
        "language": "ko",
        "completionCallback": _COMPLETION_CALLBACK,
        "executionId": "e8",
    }

    with pytest.raises(Exception):  # noqa: B017
        await activities.run(AgentJobRequest(AgentJobKind.TITLE_SUGGESTION, payload))

    assert notifier.published[-1] == (
        "user-1",
        {"jobId": "e8", "kind": "title.suggestion", "status": "failed", "taskId": "missing-task"},
    )
    execution_sql.close()
