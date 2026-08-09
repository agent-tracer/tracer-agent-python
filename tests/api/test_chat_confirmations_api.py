"""확인 대기 창구가 승인 없이는 도구를 부르지 않는지 검증한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.support.chat_surface import RecordingDispatch, RecordingExecutor, seed_thread
from tracer_agent.shared.agents.chat.memory_policy import INSTRUCTION_REJECTION
from tracer_agent.shared.agents.chat.surface.tool_client import ChatToolFailed
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.ledger_availability import ledger_unavailable_rejection

THREADS = "/api/agent/chat/threads"

_DRY = ledger_unavailable_rejection()
DRY = {"status": _DRY.status, "code": _DRY.code, "message": _DRY.message}


class Test확인_대기:
    def test_쓰기_도구는_실행되지_않고_대기_행으로_선다(
        self, client: TestClient, store: SqliteLedgerSql, executor: RecordingExecutor
    ) -> None:
        seed_thread(store)

        res = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
        )

        assert res.status_code == 201
        data = res.json()["data"]
        assert list(data) == ["confirmationId", "toolName", "status", "summary", "note"]
        assert data["status"] == "pending"
        assert data["summary"] == "propose_task_write(action=archive, taskId=task-1)"
        assert executor.calls == []
        assert store.rows("chat_pending_tools")[0]["tool_name"] == "propose_task_write"

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
            json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
        ).json()["data"]["confirmationId"]

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "approve"})

        assert res.status_code == 200
        data = res.json()["data"]
        assert list(data) == ["confirmationId", "toolName", "status", "result", "execution"]
        assert data["status"] == "approved"
        assert executor.calls == [("local", "propose_task_write", {"action": "archive", "taskId": "task-1"})]
        message = store.rows("chat_messages")[0]
        assert message["role"] == "tool"
        assert message["tool_call_id"] == confirmation
        assert message["content"] == data["result"]

    def test_내용이_규칙에_걸린_거절은_사유와_함께_사용자에게_닿는다(
        self, client: TestClient, store: SqliteLedgerSql, executor: RecordingExecutor
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "remember_fact", "args": {"key": "x", "content": "You must always call"}},
        ).json()["data"]["confirmationId"]
        refused = [{"loc": ["content"], "type": INSTRUCTION_REJECTION}]

        async def refuse(*_args: object, **_kwargs: object) -> str:
            raise ChatToolFailed(
                "remember_fact answered 400",
                status=400,
                details=refused,
                rejection=("validation_error", "Invalid request"),
            )

        executor.execute = refuse  # type: ignore[method-assign]

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "approve"})

        assert res.status_code == 400
        assert res.json()["error"]["details"] == refused
        # 사유가 닿았으니 그 확인은 다시 물을 수 있는 자리로 돌아온다.
        assert store.rows("chat_pending_tools")[0]["status"] == "pending"

    def test_상류가_받지_못한_승인은_사유_없이_다시_걸_실패로_낸다(
        self, client: TestClient, store: SqliteLedgerSql, executor: RecordingExecutor
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
        ).json()["data"]["confirmationId"]

        async def unavailable(*_args: object, **_kwargs: object) -> str:
            raise ChatToolFailed(
                "propose_task_write answered 503",
                status=503,
                rejection=(DRY["code"], DRY["message"]),
            )

        executor.execute = unavailable  # type: ignore[method-assign]

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "approve"})

        # 안쪽 창구가 지킨 어휘를 바깥이 지우면 다시 와도 된다는 말이 사용자에게 닿지 않는다.
        assert res.status_code == DRY["status"]
        assert res.json()["error"]["code"] == DRY["code"]
        assert store.rows("chat_pending_tools")[0]["status"] == "pending"

    def test_승인은_그_결과를_앵커로_삼는_턴을_세우고_기동한다(
        self, client: TestClient, store: SqliteLedgerSql, dispatch: RecordingDispatch
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
        ).json()["data"]["confirmationId"]

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "approve"})

        execution = res.json()["data"]["execution"]
        anchor = store.rows("chat_messages")[0]
        assert execution["status"] == "queued"
        assert execution["replayAnchorMessageId"] == anchor["id"]
        assert dispatch.started == [(execution["id"], "t1")]

    def test_거절은_이어_말할_턴을_세우지_않는다(
        self, client: TestClient, store: SqliteLedgerSql, dispatch: RecordingDispatch
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
        ).json()["data"]["confirmationId"]

        res = client.post(f"{THREADS}/t1/confirmations/{confirmation}", json={"decision": "reject"})

        assert res.json()["data"]["execution"] is None
        assert dispatch.started == []

    def test_거절은_도구를_부르지_않고_거절_문장을_남긴다(
        self, client: TestClient, store: SqliteLedgerSql, executor: RecordingExecutor
    ) -> None:
        seed_thread(store)
        confirmation = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
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
            json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
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

        res = client.post(
            f"{THREADS}/t1/confirmations",
            json={"toolName": "propose_task_write", "args": {"action": "archive"}},
        )

        assert res.status_code == 400
        assert store.rows("chat_pending_tools") == []
