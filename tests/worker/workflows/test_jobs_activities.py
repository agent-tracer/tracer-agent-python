"""잡 액티비티가 요청 종류에 맞는 그래프를 실제로 돌리고 완료 창구로 배달하는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeLedger, FakeSearch, FakeToolLoopChat
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.runtime.ledger import LedgerSql, PooledSql
from tracer_agent.shared.workflows.jobs_envelope import JobExecutionEnvelope
from tracer_agent.shared.workflows.jobs_ledger import JobLedger
from tracer_agent.shared.workflows.jobs_spec import AgentJobRequest
from tracer_agent.worker.agents.recipe_scan import agent as recipe_mod
from tracer_agent.worker.agents.task_cleanup import agent as cleanup_mod
from tracer_agent.worker.agents.title_suggestion import agent as title_mod
from tracer_agent.worker.workflows.jobs_activities import AgentJobActivities

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
        )


async def claim(store: SqliteLedgerSql, job_id: str) -> None:
    """액티비티가 전진시킬 대기 행 하나를 세운다."""
    await JobLedger(store).claim(job_id, "user-1", "title.suggestion", "temporal", None, None, {}, NOW)


@pytest.fixture
def http() -> CapturingCompletionClient:
    return CapturingCompletionClient()


async def test_title_suggestion_요청을_돌려_완료_창구로_배달한다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(title_mod, "make_chat", lambda *_a, **_k: FakeToolLoopChat([{"suggestions": []}]))
    activities = AgentJobActivities(FakeLedger(), FakeSearch(), http, PooledSql(FakeLedger()))  # type: ignore[arg-type]
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

    await activities.run(AgentJobRequest("title-suggestion", payload))

    assert http.deliveries[0]["token"] == "done-1"
    assert http.deliveries[0]["response"]["data"] == {"suggestions": []}


async def test_task_cleanup_요청을_돌려_완료_창구로_배달한다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cleanup_mod, "make_chat", lambda *_a, **_k: FakeToolLoopChat([{"suggestions": []}]))
    activities = AgentJobActivities(FakeLedger(), FakeSearch(), http, PooledSql(FakeLedger()))  # type: ignore[arg-type]
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

    await activities.run(AgentJobRequest("task-cleanup", payload))

    assert http.deliveries[0]["response"]["data"] == {"suggestions": []}


async def test_recipe_scan_요청을_돌려_완료_창구로_배달한다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recipe_mod, "make_chat", lambda *_a, **_k: FakeToolLoopChat([{"recipes": []}]))
    activities = AgentJobActivities(FakeLedger(), FakeSearch(), http, PooledSql(FakeLedger()))  # type: ignore[arg-type]
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

    await activities.run(AgentJobRequest("recipe-scan", payload))

    assert http.deliveries[0]["response"]["data"]["recipes"] == []


async def test_실행_식별자가_있으면_원장에_종료_상태와_비용과_관측이_남는다(
    http: CapturingCompletionClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(title_mod, "make_chat", lambda *_a, **_k: FakeToolLoopChat([{"suggestions": []}]))
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e1")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        FakeLedger(), FakeSearch(), http, _StaticSql(execution_sql)
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

    await activities.run(AgentJobRequest("title-suggestion", payload))

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
    monkeypatch.setattr(title_mod, "make_chat", lambda *_a, **_k: FakeToolLoopChat([{"suggestions": []}]))
    envelopes = FakeEnvelopeSource()
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e2")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        FakeLedger(), FakeSearch(), http, _StaticSql(execution_sql), envelopes=envelopes
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

    await activities.run(AgentJobRequest("title-suggestion", payload))

    assert envelopes.issued_for == ["title.suggestion:user-1"]
    assert http.deliveries[0]["response"]["data"] == {"suggestions": []}
    execution_sql.close()


async def test_페이로드에_자격도_실행_식별자도_없으면_거부한다(http: CapturingCompletionClient) -> None:
    activities = AgentJobActivities(FakeLedger(), FakeSearch(), http, PooledSql(FakeLedger()))  # type: ignore[arg-type]
    payload = {"model": "claude-haiku-4-5", "taskId": "task-1"}

    with pytest.raises(ValueError):
        await activities.run(AgentJobRequest("title-suggestion", payload))


async def test_실행_액티비티가_돌기_전에_취소되면_원장이_취소로_닫힌다(
    http: CapturingCompletionClient,
) -> None:
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e3")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        FakeLedger(), FakeSearch(), http, _StaticSql(execution_sql)
    )

    await activities.settle_canceled("e3")

    row = execution_sql.rows("ai_jobs")[0]
    assert row["status"] == "canceled"
    execution_sql.close()


async def test_그래프를_돌리기_전에_죽으면_원장이_failed로_닫힌다(http: CapturingCompletionClient) -> None:
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e5")
    activities = AgentJobActivities(  # type: ignore[arg-type]
        FakeLedger(owned=False), FakeSearch(), http, _StaticSql(execution_sql)
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
        await activities.run(AgentJobRequest("title-suggestion", payload))

    row = execution_sql.rows("ai_jobs")[0]
    assert row["status"] == "failed"
    execution_sql.close()


async def test_이미_종결된_행은_취소_닫기가_건드리지_않는다(http: CapturingCompletionClient) -> None:
    execution_sql = SqliteLedgerSql()
    await claim(execution_sql, "e4")
    await JobLedger(execution_sql).settle("e4", "completed", {}, {}, None, NOW)
    activities = AgentJobActivities(  # type: ignore[arg-type]
        FakeLedger(), FakeSearch(), http, _StaticSql(execution_sql)
    )

    await activities.settle_canceled("e4")

    row = execution_sql.rows("ai_jobs")[0]
    assert row["status"] == "completed"
    execution_sql.close()
