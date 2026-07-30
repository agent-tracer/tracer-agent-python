"""확인 대기 창구가 승인 없이는 도구를 부르지 않는지 검증한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.support.chat_surface import RecordingExecutor, seed_thread
from tests.support.sqlite_ledger import SqliteLedgerSql

THREADS = "/api/agent/chat/threads"


class Test확인_대기:
    def test_쓰기_도구는_실행되지_않고_대기_행으로_선다(
        self, client: TestClient, store: SqliteLedgerSql, executor: RecordingExecutor
    ) -> None:
        seed_thread(store)

        res = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "archive_task", "args": {"taskId": "task-1"}},
        )

        assert res.status_code == 201
        data = res.json()["data"]
        assert list(data) == ["confirmationId", "toolName", "status", "summary", "note"]
        assert data["status"] == "pending"
        assert data["summary"] == "archive_task(taskId=task-1)"
        assert executor.calls == []
        assert store.rows("chat_pending_tools")[0]["tool_name"] == "archive_task"

    def test_확인_게이트가_없는_도구는_대기_행에_세우지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)

        res = client.post(f"{THREADS}/t1/confirmations", json={"toolName": "get_task"})

        assert res.status_code == 400

    def test_승인은_도구를_부르고_그_결과를_대화에_남긴다(
        self, client: TestClient, store: SqliteLedgerSql, executor: RecordingExecutor
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "archive_task", "args": {"taskId": "task-1"}},
        ).json()["data"]["confirmationId"]

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "approve"})

        assert res.status_code == 200
        data = res.json()["data"]
        assert list(data) == ["confirmationId", "toolName", "status", "result"]
        assert data["status"] == "approved"
        assert executor.calls == [("local", "archive_task", {"taskId": "task-1"})]
        message = store.rows("chat_messages")[0]
        assert message["role"] == "tool"
        assert message["tool_call_id"] == confirmation
        assert message["content"] == data["result"]

    def test_거절은_도구를_부르지_않고_거절_문장을_남긴다(
        self, client: TestClient, store: SqliteLedgerSql, executor: RecordingExecutor
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "archive_task", "args": {"taskId": "task-1"}},
        ).json()["data"]["confirmationId"]

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "reject"})

        assert res.json()["data"]["status"] == "rejected"
        assert executor.calls == []
        assert "rejected" in store.rows("chat_messages")[0]["content"]

    def test_이미_해소된_확인은_다시_해소하지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "archive_task", "args": {"taskId": "task-1"}},
        ).json()["data"]["confirmationId"]
        client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "reject"})

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "reject"})

        assert res.status_code == 409

    def test_없는_확인의_결정은_404다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        seed_thread(store)

        res = client.post(f"{THREADS}/t1/confirmations/no-such", json={"decision": "approve"})

        assert res.status_code == 404

    def test_인자가_어긋난_제안은_승인_전에_거절한다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)

        res = client.post(f"{THREADS}/t1/confirmations", json={"toolName": "archive_task", "args": {}})

        assert res.status_code == 400
        assert store.rows("chat_pending_tools") == []
