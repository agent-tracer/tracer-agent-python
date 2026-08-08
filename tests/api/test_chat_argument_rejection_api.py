"""action 이 요구하는 인자가 빠진 호출을 대기 행 앞에서 거절하는지 검증한다."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.support.chat_surface import seed_thread
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT

DECLARED = json.loads((CONTRACT_ROOT / "agent" / "chat" / "tool.json").read_text(encoding="utf-8"))
REJECTION = DECLARED["argumentRejection"]

CONFIRMATIONS_PATH = "/api/agent/chat/threads/t1/confirmations"
HEADERS = {"x-user-id": "local"}


def test_action_이_요구하는_인자가_빠지면_대기_행을_세우지_않고_거절한다(
    client: TestClient, store: SqliteLedgerSql
) -> None:
    seed_thread(store)

    res = client.post(
        CONFIRMATIONS_PATH,
        json={"toolName": "propose_task_write", "args": {"action": "update"}},
        headers=HEADERS,
    )

    assert res.status_code == REJECTION["status"]
    assert res.json()["error"]["code"] == REJECTION["code"]
    assert res.json()["error"]["details"] == {"action": "update", "missing": ["taskId"]}
    assert store.rows("chat_pending_tools") == []


def test_같은_action_이_인자를_갖추면_대기_행이_선다(client: TestClient, store: SqliteLedgerSql) -> None:
    seed_thread(store)

    res = client.post(
        CONFIRMATIONS_PATH,
        json={"toolName": "propose_task_write", "args": {"action": "archive", "taskId": "task-1"}},
        headers=HEADERS,
    )

    assert res.status_code == 201
    assert len(store.rows("chat_pending_tools")) == 1


def test_빈_목록도_빠진_것으로_보고_거절한다(client: TestClient, store: SqliteLedgerSql) -> None:
    seed_thread(store)

    res = client.post(
        CONFIRMATIONS_PATH,
        json={
            "toolName": "propose_tag_write",
            "args": {"action": "assign", "taskId": "task-1", "tagIds": []},
        },
        headers=HEADERS,
    )

    assert res.json()["error"]["details"] == {"action": "assign", "missing": ["tagIds"]}
    assert store.rows("chat_pending_tools") == []


def test_계약이_표를_갖는_도구는_그_표의_action_을_모두_덮는다() -> None:
    for name, tool in DECLARED["tools"].items():
        table = tool.get("requiredByAction")
        if table is None:
            continue
        declared_actions = set(tool["args"]["action"]["values"])
        assert set(table) == declared_actions, name
