"""열린 연결이 실행 스냅샷을 흘려보내고 종결에서 닫는지 검증한다."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient

from tests.support.chat_surface import NOW, seed_execution, seed_pending_tool, seed_thread
from tests.support.sqlite_ledger import SqliteLedgerSql

THREADS = "/api/agent/chat/threads"


def _frames(body: str) -> list[dict[str, object]]:
    return [json.loads(chunk.split("data: ", 1)[1]) for chunk in body.split("\n\n") if "data: " in chunk]


class Test실행_스냅샷_스트림:
    def test_종결_상태의_스냅샷을_한_번_보내고_연결을_닫는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(
            store,
            "e1",
            status="completed",
            draft_text="끝난 답변",
            draft_seq=4,
            updated_at=NOW + timedelta(seconds=2),
        )
        seed_pending_tool(store)

        with client.stream("GET", f"{THREADS}/t1/executions/e1/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())

        frames = _frames(body)
        assert len(frames) == 1
        assert body.splitlines()[0] == "event: snapshot"
        assert frames[0]["execution"]["status"] == "completed"  # type: ignore[index]
        assert frames[0]["confirmations"] == [
            {"id": "c1", "toolName": "archive_task", "args": {"taskId": "task-1"}}
        ]

    def test_남의_실행의_연결은_열지_않는다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        seed_thread(store)
        seed_execution(store, "e1", thread_id="other", status="completed")

        res = client.get(f"{THREADS}/t1/executions/e1/events")

        assert res.status_code == 404
        assert res.json()["error"]["code"] == "not_found"

    def test_해소된_대기_도구는_프레임에_싣지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_execution(store, "e1", status="failed")
        seed_pending_tool(store, "c1", status="approved")

        with client.stream("GET", f"{THREADS}/t1/executions/e1/events") as response:
            body = "".join(response.iter_text())

        assert _frames(body)[0]["confirmations"] == []
