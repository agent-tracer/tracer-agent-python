"""대화 실행의 이력과 궤적과 되읽기 창구가 소유자에게만 답하는지 검증한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.support.chat_surface import (
    seed_execution,
    seed_memory,
    seed_message,
    seed_pending_tool,
    seed_step,
    seed_thread,
)
from tests.support.contract import conformance_case
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql

THREADS = "/api/agent/chat/threads"

EXECUTION_FIELDS = conformance_case("chat.query")["shapes"]["execution"]["fields"]


class Test실행_조회:
    def test_실행_이력은_최근순으로_나오고_대기_도구를_함께_싣는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(store, "e1", offset=0, status="completed")
        seed_execution(store, "e2", offset=10)
        seed_pending_tool(store, "c1")
        seed_pending_tool(
            store, "c2", status="approved", tool_name="propose_task_write", args={"action": "delete"}
        )

        data = client.get(f"{THREADS}/t1/executions").json()["data"]

        assert [execution["id"] for execution in data["items"]] == ["e2", "e1"]
        assert list(data["items"][0]) == EXECUTION_FIELDS
        assert data["confirmations"] == [
            {"id": "c1", "toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}}
        ]

    def test_궤적은_시도와_순번의_오름차순으로_낸다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store)
        for step_id, (attempt, seq) in enumerate([(2, 0), (1, 1), (1, 0)]):
            seed_step(store, f"s{step_id}", attempt, seq)

        items = client.get(f"{THREADS}/t1/executions/e1/steps").json()["data"]["items"]

        assert [(step["attempt"], step["seq"]) for step in items] == [(1, 0), (1, 1), (2, 0)]
        assert items[0]["toolCalls"] == []
        assert "toolName" not in items[0]

    def test_남의_스레드에_걸린_실행은_존재_여부도_드러내지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(store, "e1", thread_id="other")

        assert client.get(f"{THREADS}/t1/executions/e1/steps").status_code == 404
        assert client.get(f"{THREADS}/t1/executions/e1/replay").status_code == 404


class Test되읽기:
    def test_되읽기는_이번_턴까지의_이력과_요약과_기억을_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store, summary="지난 이야기")
        seed_message(store, "m1", "user", "안녕", offset=0)
        seed_message(store, "m2", "assistant", "네", offset=1)
        seed_message(store, "m3", "user", "이어서", offset=2)
        seed_message(store, "m4", "assistant", "아직", offset=3)
        seed_execution(store, "e1", replay_anchor_message_id="m3")
        seed_memory(store)

        data = client.get(f"{THREADS}/t1/executions/e1/replay").json()["data"]

        assert [message["content"] for message in data["messages"]] == ["안녕", "네", "이어서"]
        assert data["summary"] == "지난 이야기"
        assert data["facts"] == [{"key": "lang", "content": "한국어를 쓴다"}]

    def test_이력에_없는_사용자_메시지를_가리키면_404다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(store, "e1", replay_anchor_message_id="없음")

        res = client.get(f"{THREADS}/t1/executions/e1/replay")

        assert res.status_code == 404
        assert res.json()["error"]["code"] == "not_found"
