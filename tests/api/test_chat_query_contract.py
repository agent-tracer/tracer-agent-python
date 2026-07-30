"""대화 조회 표면이 적합성 케이스가 소유한 칸과 순서와 거절을 그대로 내는지 대조한다."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from tests.support.chat_surface import (
    DRAFT_TOKEN,
    DRAFT_TOKEN_HASH,
    NOW,
    seed_execution,
    seed_memory,
    seed_message,
    seed_pending_tool,
    seed_step,
    seed_thread,
)
from tests.support.contract import conformance_case
from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.chat.models import (
    CHAT_CONFIRMATION_STATUSES,
    CHAT_EXECUTION_STATUSES,
    CHAT_MESSAGE_ROLES,
    CHAT_STOP_REASONS,
    TERMINAL_CHAT_EXECUTION_STATUSES,
)
from tracer_agent.shared.agents.chat.surface.stream import HEARTBEAT_S, SNAPSHOT_EVENT
from tracer_agent.shared.agents.shared.models import AgentStepRole, GraphEventKind

THREADS = "/api/agent/chat/threads"
MEMORIES = "/api/agent/chat/memories"

CASE = conformance_case("chat.query")
SHAPES = CASE["shapes"]
WINDOWS = {(window["method"], window["path"]): window for window in CASE["windows"]}


def _literals(annotation: Any) -> tuple[str, ...]:
    return tuple(str(one) for one in get_args(annotation))


def _shape_fields(name: str) -> list[str]:
    fields: list[str] = SHAPES[name]["fields"]
    return fields


def _seed_all(store: SqliteLedgerSql) -> None:
    """케이스가 대조하는 창구 전부가 값을 실어 낼 만큼의 원장을 심는다."""
    seed_thread(store, summary="지난 이야기")
    seed_message(store, "m1", "user", "안녕", offset=0)
    seed_message(
        store,
        "m2",
        "assistant",
        "부른다",
        offset=1,
        tool_calls=[{"id": "call-1", "name": "archive_task", "args": {"taskId": "task-1"}}],
    )
    seed_message(store, "m3", "tool", "결과", offset=2, tool_call_id="call-1")
    seed_message(store, "m4", "user", "이어서", offset=3)
    seed_execution(store, "e1", user_message_id="m4", draft_token_hash=DRAFT_TOKEN_HASH)
    seed_pending_tool(store)
    seed_step(
        store,
        "s1",
        1,
        0,
        tool_calls=[{"id": "call-1", "name": "archive_task", "args": {}}],
        tool_name="archive_task",
        tool_call_id="call-1",
        input_tokens=1,
        output_tokens=2,
        cache_read_tokens=3,
        cache_creation_tokens=4,
        stop_reason="tool_use",
        node_name="converse",
        event_kind="node.started",
        duration_ms=5,
    )
    seed_step(store, "s2", 1, 1, role="tool", content="빈 자리는 싣지 않는다")
    seed_memory(store)


class Test열거:
    def test_메시지_역할이_케이스와_같다(self) -> None:
        assert list(CHAT_MESSAGE_ROLES) == CASE["enums"]["messageRole"]

    def test_궤적_역할이_케이스와_같다(self) -> None:
        assert list(_literals(AgentStepRole)) == CASE["enums"]["stepRole"]

    def test_궤적_사건_종류가_케이스와_같다(self) -> None:
        assert list(_literals(GraphEventKind)) == CASE["enums"]["stepEventKind"]

    def test_실행_상태가_케이스와_같다(self) -> None:
        assert list(CHAT_EXECUTION_STATUSES) == CASE["enums"]["executionStatus"]

    def test_종결_상태가_케이스와_같다(self) -> None:
        assert list(TERMINAL_CHAT_EXECUTION_STATUSES) == CASE["enums"]["terminalExecutionStatus"]

    def test_멈춘_이유가_케이스와_같다(self) -> None:
        assert list(CHAT_STOP_REASONS) == CASE["enums"]["stopReason"]

    def test_확인_상태가_케이스와_같다(self) -> None:
        assert list(CHAT_CONFIRMATION_STATUSES) == CASE["enums"]["confirmationStatus"]


class Test창구의_칸:
    @pytest.fixture(autouse=True)
    def _seeded(self, store: SqliteLedgerSql) -> None:
        _seed_all(store)

    def _data(self, response: Any) -> Any:
        assert response.status_code == WINDOWS[self.window]["status"], response.text
        body = response.json()
        assert body["ok"] is True
        return body["data"]

    def _assert_shape(self, data: Any, spec: Any) -> None:
        if isinstance(spec, str):
            assert list(data) == _shape_fields(spec)
            return
        assert list(data) == list(spec)
        for key, value in spec.items():
            self._assert_slot(data[key], value)

    def _assert_slot(self, value: Any, notation: str) -> None:
        if notation == "boolean":
            assert isinstance(value, bool)
        elif notation == "true":
            assert value is True
        elif notation.endswith("[]"):
            assert isinstance(value, list)
            for one in value:
                assert list(one) == _shape_fields(notation[:-2])
        elif notation.endswith("?"):
            assert value is None or list(value) == _shape_fields(notation[:-1])
        else:
            assert list(value) == _shape_fields(notation)

    def test_스레드_목록이_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("GET", "/api/agent/chat/threads")
        self._assert_shape(self._data(client.get(THREADS)), WINDOWS[self.window]["data"])

    def test_스레드_개설이_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("POST", "/api/agent/chat/threads")
        data = self._data(client.post(THREADS, json={"title": "새 대화"}))
        self._assert_shape(data, WINDOWS[self.window]["data"])

    def test_스레드_상세가_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("GET", "/api/agent/chat/threads/{threadId}")
        self._assert_shape(self._data(client.get(f"{THREADS}/t1")), WINDOWS[self.window]["data"])

    def test_스레드_개명이_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("PATCH", "/api/agent/chat/threads/{threadId}")
        data = self._data(client.patch(f"{THREADS}/t1", json={"title": "고침"}))
        self._assert_shape(data, WINDOWS[self.window]["data"])

    def test_스레드_삭제가_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("DELETE", "/api/agent/chat/threads/{threadId}")
        self._assert_shape(self._data(client.delete(f"{THREADS}/t1")), WINDOWS[self.window]["data"])

    def test_메시지_목록이_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("GET", "/api/agent/chat/threads/{threadId}/messages")
        data = self._data(client.get(f"{THREADS}/t1/messages"))
        self._assert_shape(data, WINDOWS[self.window]["data"])

    def test_실행_이력이_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("GET", "/api/agent/chat/threads/{threadId}/executions")
        data = self._data(client.get(f"{THREADS}/t1/executions"))
        self._assert_shape(data, WINDOWS[self.window]["data"])
        assert len(data["items"][0]) == len(_shape_fields("execution")) == 17

    def test_되읽기가_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("GET", "/api/agent/chat/threads/{threadId}/executions/{executionId}/replay")
        data = self._data(client.get(f"{THREADS}/t1/executions/e1/replay"))
        self._assert_shape(data, WINDOWS[self.window]["data"])
        for fact in data["facts"]:
            assert list(fact) == _shape_fields("userFact")

    def test_확인_대기_세우기가_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("POST", "/api/agent/chat/threads/{threadId}/confirmations")
        data = self._data(
            client.post(
                f"{THREADS}/t1/confirmations",
                json={"toolName": "archive_task", "args": {"taskId": "task-1"}},
            )
        )
        self._assert_shape(data, WINDOWS[self.window]["data"])
        assert data["status"] in CASE["enums"]["confirmationStatus"]

    def test_확인_해소가_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("POST", "/api/agent/chat/threads/{threadId}/confirmations/{confirmationId}")
        data = self._data(client.post(f"{THREADS}/t1/confirmations/c1", json={"decision": "reject"}))
        self._assert_shape(data, WINDOWS[self.window]["data"])

    def test_누적_답변_통지가_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = ("POST", "/api/agent/chat/executions/{executionId}/drafts")
        data = self._data(
            client.post(
                "/api/agent/chat/executions/e1/drafts",
                json={"token": DRAFT_TOKEN, "attempt": 1, "draftSeq": 1, "text": "쌓이는 답변"},
            )
        )
        self._assert_shape(data, WINDOWS[self.window]["data"])

    def test_턴_중단이_케이스가_적은_칸을_낸다(self, client: TestClient) -> None:
        self.window = (
            "POST",
            "/api/agent/chat/threads/{threadId}/executions/{executionId}/cancel",
        )
        data = self._data(client.post(f"{THREADS}/t1/executions/e1/cancel"))
        self._assert_shape(data, WINDOWS[self.window]["data"])


class Test궤적의_칸:
    @pytest.fixture(autouse=True)
    def _seeded(self, store: SqliteLedgerSql) -> None:
        _seed_all(store)

    def test_궤적은_필수_칸을_늘_싣고_값이_없는_칸은_싣지_않는다(self, client: TestClient) -> None:
        spec = SHAPES["step"]
        items = client.get(f"{THREADS}/t1/executions/e1/steps").json()["data"]["items"]

        assert [(step["attempt"], step["seq"]) for step in items] == [(1, 0), (1, 1)]
        assert list(items[0]) == spec["required"] + spec["optional"]
        assert list(items[1]) == spec["required"]

    def test_재생_메시지도_값이_없는_칸을_싣지_않는다(self, client: TestClient) -> None:
        spec = SHAPES["replayMessage"]
        messages = client.get(f"{THREADS}/t1/executions/e1/replay").json()["data"]["messages"]

        assert all(set(spec["required"]) <= set(message) for message in messages)
        assert all(set(message) <= set(spec["required"]) | set(spec["optional"]) for message in messages)
        assert [message.get("toolCalls") is not None for message in messages] == [
            False,
            True,
            False,
            False,
        ]


class Test재생_규칙:
    @pytest.fixture(autouse=True)
    def _seeded(self, store: SqliteLedgerSql) -> None:
        _seed_all(store)

    def test_이번_턴의_사용자_메시지까지만_싣는다(self, client: TestClient) -> None:
        seed_message_ids = ["안녕", "부른다", "결과", "이어서"]
        data = client.get(f"{THREADS}/t1/executions/e1/replay").json()["data"]

        assert [message["content"] for message in data["messages"]] == seed_message_ids

    def test_요약이_있으면_그_값을_함께_낸다(self, client: TestClient) -> None:
        data = client.get(f"{THREADS}/t1/executions/e1/replay").json()["data"]

        assert data["summary"] == "지난 이야기"

    def test_짝을_잃은_도구_결과는_인용만_벗기고_남는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_message(store, "m5", "tool", "짝 없는 결과", offset=4, tool_call_id="call-없음")
        seed_message(store, "m6", "user", "그다음", offset=5)
        seed_execution(store, "e2", user_message_id="m6", status="completed")

        messages = client.get(f"{THREADS}/t1/executions/e2/replay").json()["data"]["messages"]

        orphan = next(message for message in messages if message["content"] == "짝 없는 결과")
        assert "toolCallId" not in orphan

    def test_결과를_못_받은_호출만_남은_메시지는_싣지_않는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_message(
            store,
            "m5",
            "assistant",
            "",
            offset=4,
            tool_calls=[{"id": "call-2", "name": "delete_task", "args": {}}],
        )
        seed_message(store, "m6", "user", "그다음", offset=5)
        seed_execution(store, "e2", user_message_id="m6", status="completed")

        messages = client.get(f"{THREADS}/t1/executions/e2/replay").json()["data"]["messages"]

        assert all(message["content"] != "" for message in messages)


class Test스트림:
    @pytest.fixture(autouse=True)
    def _seeded(self, store: SqliteLedgerSql) -> None:
        _seed_all(store)
        seed_execution(
            store,
            "e-done",
            user_message_id="m4",
            status="completed",
            draft_text="끝난 답변",
            draft_seq=7,
            updated_at=NOW + timedelta(seconds=2),
        )

    def test_주기_다시_읽기가_케이스가_적은_간격이다(self) -> None:
        assert int(HEARTBEAT_S * 1000) == CASE["stream"]["resendIntervalMs"]

    def test_사건_이름이_케이스가_적은_이름이다(self) -> None:
        assert CASE["stream"]["event"] == SNAPSHOT_EVENT

    def test_종결_스냅샷_한_프레임을_보낸_뒤_연결을_닫는다(self, client: TestClient) -> None:
        with client.stream("GET", f"{THREADS}/t1/executions/e-done/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(CASE["stream"]["contentType"])
            body = "".join(response.iter_text())

        assert body.count("event: snapshot") == 1
        assert "id: 7:2026-07-30T00:00:02.000Z" in body
        frame = json.loads(body.split("data: ", 1)[1].strip())
        assert list(frame) == _shape_fields("snapshot")
        assert list(frame["execution"]) == _shape_fields("execution")
        for confirmation in frame["confirmations"]:
            assert list(confirmation) == _shape_fields("confirmation")

    def test_남의_실행의_스트림은_열지_않는다(self, client: TestClient) -> None:
        res = client.get(f"{THREADS}/t1/executions/no-such/events")

        assert res.status_code == CASE["notFound"]["status"]
        assert res.json()["error"]["code"] == CASE["notFound"]["code"]


class Test거절:
    @pytest.fixture(autouse=True)
    def _seeded(self, store: SqliteLedgerSql) -> None:
        _seed_all(store)

    def test_없는_스레드는_케이스가_적은_문장으로_거절한다(self, client: TestClient) -> None:
        res = client.get(f"{THREADS}/no-such")

        assert res.status_code == CASE["notFound"]["status"]
        error = res.json()["error"]
        assert error["code"] == CASE["notFound"]["code"]
        assert error["message"] == CASE["notFound"]["messages"]["thread"]

    def test_없는_실행은_케이스가_적은_문장으로_거절한다(self, client: TestClient) -> None:
        res = client.get(f"{THREADS}/t1/executions/no-such/steps")

        error = res.json()["error"]
        assert error["code"] == CASE["notFound"]["code"]
        assert error["message"] == CASE["notFound"]["messages"]["execution"]

    def test_본문이_스키마를_어기면_케이스가_적은_거절을_낸다(self, client: TestClient) -> None:
        rejection = next(one for one in CASE["rejections"] if one["status"] == 400)

        res = client.post(THREADS, json={})

        assert res.status_code == rejection["status"]
        assert res.json()["error"]["code"] == rejection["code"]
        assert res.json()["error"]["message"] == rejection["message"]

    def test_다른_시도의_토큰은_케이스가_적은_거절을_낸다(self, client: TestClient) -> None:
        rejection = next(one for one in CASE["rejections"] if one["status"] == 403)

        res = client.post(
            "/api/agent/chat/executions/e1/drafts",
            json={"token": "다른 토큰", "attempt": 1, "draftSeq": 1, "text": "답변"},
        )

        assert res.status_code == rejection["status"]
        assert res.json()["error"]["code"] == rejection["code"]
        assert res.json()["error"]["message"] == rejection["message"]


class Test사용자_헤더:
    def test_헤더가_비면_케이스가_적은_기본_사용자로_읽는다(
        self, client: TestClient, store: SqliteLedgerSql
    ) -> None:
        seed_thread(store, "t1", user_id=CASE["userHeader"]["defaultValue"])

        assert client.get(f"{THREADS}/t1", headers={CASE["userHeader"]["name"]: "  "}).status_code == 200

    def test_기억_창구도_같은_헤더로_사용자를_가른다(self, client: TestClient) -> None:
        client.put(f"{MEMORIES}/lang", json={"content": "한국어를 쓴다"})

        other = client.get(MEMORIES, headers={CASE["userHeader"]["name"]: "u2"}).json()["data"]

        assert other["facts"] == []
