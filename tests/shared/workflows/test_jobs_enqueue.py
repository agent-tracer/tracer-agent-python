"""잡 접수 객체가 심사와 멱등 판정과 워크플로 기동을 창구 없이도 소유하는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.fakes import FakeScanAnchors
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.workflows.jobs_enqueue import (
    IDEMPOTENCY_CONFLICT,
    AcceptedJob,
    JobEnqueueBody,
    JobIntake,
    JobRejected,
)

NOW = datetime(2026, 8, 8, tzinfo=UTC)
USER = "user-1"


class FakeJobDispatch:
    """기동 요청만 보관해 두는 잡 디스패치 대역이다."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str, dict[str, Any]]] = []

    async def start(self, kind: str, key: str, payload: dict[str, Any]) -> None:
        self.started.append((str(kind), key, payload))

    async def cancel(self, _kind: str, _key: str) -> bool:
        return True


class FakeCredentials:
    """미리 정한 모델 자격만 내주는 창구 대역이다."""

    def __init__(self, key: str | None = "sk-test") -> None:
        self.key = key

    async def api_key(self, _user_id: str) -> str | None:
        return self.key


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


def _intake(dispatch: FakeJobDispatch, credentials: FakeCredentials | None = None) -> JobIntake:
    return JobIntake(FakeScanAnchors(), credentials or FakeCredentials(), dispatch)  # type: ignore[arg-type]


async def _enqueue(intake: JobIntake, store: SqliteLedgerSql, body: JobEnqueueBody) -> AcceptedJob:
    """창구가 하는 대로 심사와 원장 claim과 워크플로 기동을 차례로 실행한다."""
    admitted = await intake.admit(USER, body)
    accepted = await intake.claim(store, USER, body, admitted, NOW)
    await intake.start(body.kind, accepted)
    return accepted


def _body(**overrides: Any) -> JobEnqueueBody:
    fields: dict[str, Any] = {"kind": "task.cleanup", "input": {}}
    fields.update(overrides)
    return JobEnqueueBody.model_validate(fields)


async def test_접수는_원장_행을_세우고_워크플로를_기동한다(store: SqliteLedgerSql) -> None:
    dispatch = FakeJobDispatch()

    accepted = await _enqueue(_intake(dispatch), store, _body())

    assert accepted.row["status"] == "pending"
    assert [started[1] for started in dispatch.started] == [str(accepted.row["id"])]


async def test_자격이_없으면_대기_행을_세우지_않는다(store: SqliteLedgerSql) -> None:
    dispatch = FakeJobDispatch()

    with pytest.raises(JobRejected) as rejected:
        await _enqueue(_intake(dispatch, FakeCredentials(None)), store, _body())

    assert rejected.value.status == 400
    assert store.rows("ai_jobs") == []
    assert dispatch.started == []


async def test_스키마를_어긴_입력은_근거와_함께_거절한다(store: SqliteLedgerSql) -> None:
    with pytest.raises(JobRejected) as rejected:
        await _enqueue(_intake(FakeJobDispatch()), store, _body(kind="title.suggestion", input={}))

    assert rejected.value.status == 400
    assert rejected.value.details is not None


async def test_같은_멱등키의_같은_입력은_같은_행을_돌려준다(store: SqliteLedgerSql) -> None:
    dispatch = FakeJobDispatch()
    intake = _intake(dispatch)

    first = await _enqueue(intake, store, _body(idempotencyKey="key-1"))
    second = await _enqueue(intake, store, _body(idempotencyKey="key-1"))

    assert first.row["id"] == second.row["id"]
    assert len(store.rows("ai_jobs")) == 1


async def test_같은_멱등키의_다른_입력은_충돌로_거절한다(store: SqliteLedgerSql) -> None:
    intake = _intake(FakeJobDispatch())
    await _enqueue(intake, store, _body(idempotencyKey="key-1"))

    with pytest.raises(JobRejected) as rejected:
        await _enqueue(intake, store, _body(idempotencyKey="key-1", input={"filters": {"maxSuggestions": 3}}))

    assert (rejected.value.status, rejected.value.code) == IDEMPOTENCY_CONFLICT[:2]
