"""대화 스레드와 그 메시지의 창구가 소유자에게만 답하는지 검증한다."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from tests.support.chat_surface import (
    NOW,
    RecordingDispatch,
    seed_execution,
    seed_message,
    seed_thread,
)
from tests.support.contract import conformance_case
from tests.support.sqlite_ledger import SqliteLedgerSql

THREADS = "/api/v1/chat/threads"

_SHAPES = conformance_case("chat.query")["shapes"]
THREAD_FIELDS = _SHAPES["thread"]["fields"]
MESSAGE_FIELDS = _SHAPES["message"]["fields"]


class Test스레드_창구:
    def test_새_대화를_열고_그_행을_계약이_정한_칸으로_낸다(self, client: TestClient) -> None:
        res = client.post(THREADS, json={"title": "  새 대화  "})

        assert res.status_code == 201
        thread = res.json()["data"]["thread"]
        assert list(thread) == THREAD_FIELDS
        assert thread["title"] == "새 대화"
        assert thread["summary"] is None
        assert thread["backend"] is None
        assert thread["createdAt"].endswith("Z")

    def test_제목이_비면_접수를_거절한다(self, client: TestClient) -> None:
        res = client.post(THREADS, json={"title": "   "})

        assert res.status_code == 400
        assert res.json()["error"]["code"] == "validation_error"

    def test_스레드_목록은_최근_갱신순으로_이_사용자의_것만_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store, "t1", updated_at=NOW)
        seed_thread(store, "t2", updated_at=NOW + timedelta(minutes=1))
        seed_thread(store, "t3", user_id="u2")

        items = client.get(THREADS).json()["data"]["items"]

        assert [thread["id"] for thread in items] == ["t2", "t1"]

    def test_남의_스레드는_존재_여부도_드러내지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store, "t1", user_id="u2")

        assert client.get(f"{THREADS}/t1").status_code == 404
        assert client.get(f"{THREADS}/t1/messages").status_code == 404
        assert client.patch(f"{THREADS}/t1", json={"title": "고침"}).status_code == 404
        assert client.delete(f"{THREADS}/t1").status_code == 404
        assert client.get(f"{THREADS}/t1/executions").status_code == 404

    def test_제목을_고치면_고친_행을_낸다(self, client: TestClient, store: SqliteLedgerSql) -> None:
        seed_thread(store)

        res = client.patch(f"{THREADS}/t1", json={"title": "고친 제목"})

        assert res.status_code == 200
        assert res.json()["data"]["thread"]["title"] == "고친 제목"

    def test_스레드를_지우면_메시지와_실행이_함께_사라지고_도는_턴을_끊는다(
        self, client: TestClient, store: SqliteLedgerSql, dispatch: RecordingDispatch
    ) -> None:
        seed_thread(store)
        seed_message(store, "m1", "user", "안녕")
        seed_execution(store)

        res = client.delete(f"{THREADS}/t1")

        assert res.status_code == 200
        assert res.json()["data"] == {"deleted": True}
        assert store.rows("chat_threads") == []
        assert store.rows("chat_messages") == []
        assert store.rows("chat_executions") == []
        assert dispatch.canceled == ["e1"]

    def test_메시지_목록은_쌓인_순서대로_계약이_정한_칸으로_낸다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store)
        seed_message(store, "m2", "assistant", "네", offset=1)
        seed_message(store, "m1", "user", "안녕", offset=0)

        items = client.get(f"{THREADS}/t1/messages").json()["data"]["items"]

        assert [message["id"] for message in items] == ["m1", "m2"]
        assert list(items[0]) == MESSAGE_FIELDS
