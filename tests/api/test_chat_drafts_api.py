"""누적 답변 창구가 살아 있는 시도의 통지만 원장에 적는지 검증한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.support.chat_surface import (
    DRAFT_TOKEN,
    DRAFT_TOKEN_HASH,
    RecordingUpdates,
    seed_execution,
    seed_thread,
)
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql


class Test누적_답변:
    def test_살아_있는_시도의_통지는_원장을_갱신하고_연결을_깨운다(
        self, client: TestClient, store: SqliteLedgerSql, updates: RecordingUpdates
    ) -> None:
        seed_thread(store)
        seed_execution(store, "e1", draft_token_hash=DRAFT_TOKEN_HASH, attempt=1, status="running")

        res = client.post(
            "/api/agent/chat/executions/e1/drafts",
            json={
                "token": DRAFT_TOKEN,
                "attempt": 1,
                "draftSeq": 3,
                "text": "쌓이는 답변",
                "phase": "responding",
            },
        )

        assert res.status_code == 200
        assert res.json()["data"] == {"stored": True, "terminal": False}
        assert store.rows("chat_executions")[0]["draft_text"] == "쌓이는 답변"
        assert updates.published == ["e1"]

    def test_뒤처진_순번의_통지는_원장을_갱신하지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(
            store, "e1", draft_token_hash=DRAFT_TOKEN_HASH, draft_seq=5, draft_text="이미 쌓인 답변"
        )

        res = client.post(
            "/api/agent/chat/executions/e1/drafts",
            json={
                "token": DRAFT_TOKEN,
                "attempt": 1,
                "draftSeq": 4,
                "text": "뒤처진 답변",
                "phase": "responding",
            },
        )

        assert res.json()["data"]["stored"] is False
        assert store.rows("chat_executions")[0]["draft_text"] == "이미 쌓인 답변"

    def test_종결된_실행은_통지에_종결을_알린다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", draft_token_hash=DRAFT_TOKEN_HASH, status="canceled")

        res = client.post(
            "/api/agent/chat/executions/e1/drafts",
            json={
                "token": DRAFT_TOKEN,
                "attempt": 1,
                "draftSeq": 1,
                "text": "늦은 답변",
                "phase": "responding",
            },
        )

        assert res.json()["data"] == {"stored": False, "terminal": True}

    def test_다른_시도의_토큰은_403이다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", draft_token_hash="other")

        res = client.post(
            "/api/agent/chat/executions/e1/drafts",
            json={"token": "grant", "attempt": 1, "draftSeq": 1, "text": "답변", "phase": "responding"},
        )

        assert res.status_code == 403
        assert res.json()["error"]["code"] == "forbidden"

    def test_없는_실행의_통지는_404다(self, client: TestClient) -> None:
        res = client.post(
            "/api/agent/chat/executions/no-such/drafts",
            json={"token": "grant", "attempt": 1, "draftSeq": 1, "text": "답변", "phase": "responding"},
        )

        assert res.status_code == 404
