"""브라우저가 치는 접수 창구가 tracer-api와 같은 상태와 봉투를 내는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.chat.intake.dispatch import UnwiredExecutionDispatch
from tracer_agent.shared.agents.runtime.ledger import LedgerSql

PATH = "/api/agent/chat/threads/t1/messages"
BODY: dict[str, Any] = {"clientRequestId": "r1", "content": "안녕"}


class SingleSql:
    """테스트 하나가 쓰는 메모리 원장을 접수 창구에 그대로 빌려 준다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store

    def connect(self) -> AbstractAsyncContextManager[LedgerSql]:
        """빌릴 때마다 같은 메모리 원장을 낸다."""
        return self._lend()

    @asynccontextmanager
    async def _lend(self) -> AsyncIterator[LedgerSql]:
        yield self._store


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    ledger.seed(
        "chat_threads",
        [
            {
                "id": "t1",
                "user_id": "local",
                "title": "대화",
                "created_at": datetime(2026, 7, 26, tzinfo=UTC),
                "updated_at": datetime(2026, 7, 26, tzinfo=UTC),
            }
        ],
    )
    yield ledger
    ledger.close()


@pytest.fixture
def client(store: SqliteLedgerSql) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.execution_sql = SingleSql(store)
        test_client.app.state.execution_dispatch = UnwiredExecutionDispatch()
        yield test_client


def test_접수는_202와_성공_봉투를_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.post(PATH, json=BODY)

    assert res.status_code == 202
    body = res.json()
    assert body["ok"] is True
    execution = store.rows("chat_executions")[0]
    assert body["data"]["execution"]["id"] == execution["id"]
    assert body["data"]["execution"]["status"] == "queued"
    assert body["data"]["execution"]["draftSeq"] == 0
    assert body["data"]["execution"]["assistantMessageId"] is None
    assert body["data"]["execution"]["createdAt"].endswith("Z")
    assert body["data"]["message"]["role"] == "user"
    assert body["data"]["message"]["content"] == "안녕"
    assert body["data"]["message"]["toolCalls"] is None


def test_자기신고_헤더가_없으면_기본_사용자로_읽는다(client: TestClient) -> None:
    assert client.post(PATH, json=BODY).status_code == 202
    assert client.post(PATH, json=BODY, headers={"x-monitor-user": "u2"}).status_code == 404


def test_남의_스레드는_404_오류_봉투를_낸다(client: TestClient) -> None:
    res = client.post(PATH, json=BODY, headers={"x-monitor-user": "u2"})

    assert res.status_code == 404
    assert res.json() == {"ok": False, "error": {"code": "not_found", "message": "Thread not found"}}


def test_같은_요청_식별자로_다른_입력이면_409_오류_봉투를_낸다(client: TestClient) -> None:
    client.post(PATH, json=BODY)

    res = client.post(PATH, json={**BODY, "content": "다른 말"})

    assert res.status_code == 409
    assert res.json() == {
        "ok": False,
        "error": {
            "code": "chat.execution-idempotency-conflict",
            "message": "Client request id was already used with different chat input",
        },
    }


def test_본문이_스키마를_어기면_400_오류_봉투를_낸다(client: TestClient) -> None:
    res = client.post(PATH, json={"clientRequestId": "", "content": "안녕"})

    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Invalid request"
    assert error["details"]


def test_본문이_JSON_객체가_아니면_400_오류_봉투를_낸다(client: TestClient) -> None:
    res = client.post(PATH, content=b"[]", headers={"content-type": "application/json"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_취소는_실행을_닫고_같은_봉투를_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    accepted = client.post(PATH, json=BODY).json()["data"]["execution"]

    res = client.post(f"/api/agent/chat/threads/t1/executions/{accepted['id']}/cancel")

    assert res.status_code == 200
    assert res.json()["data"]["execution"]["status"] == "canceled"
    assert store.rows("chat_executions")[0]["status"] == "canceled"


def test_남의_실행은_취소도_404다(client: TestClient) -> None:
    accepted = client.post(PATH, json=BODY).json()["data"]["execution"]

    res = client.post(
        f"/api/agent/chat/threads/t1/executions/{accepted['id']}/cancel",
        headers={"x-monitor-user": "u2"},
    )

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_없는_실행의_취소는_404다(client: TestClient) -> None:
    res = client.post("/api/agent/chat/threads/t1/executions/없는실행/cancel")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
