"""프롬프트 등록 창구가 계약이 정한 경로와 상태와 봉투를 내는지 검증한다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.support.sqlite_ledger import SqliteLedgerSql
from tracer_agent.api import app as app_module
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.shared.fragment_integrity import fragment_content_hash

FRAGMENTS_PATH = "/internal/prompts/fragments/register-and-resolve"
REGISTER_PATH = "/internal/prompts/python/register"
CONTENT = "Keep the ${taskId} the reviewers cited."

ENTRY: dict[str, Any] = {
    "backend": "python",
    "agentName": "task-cleanup",
    "language": "en",
    "codeName": "TASK_CLEANUP_REPAIR_DIRECTIVE",
    "definitionKey": "task-cleanup.repair-directive.en",
    "fragmentName": "repairDirective",
    "defaultVersion": "v1",
    "defaultContent": CONTENT,
    "toolContractVersion": "1",
    "outputSchemaVersion": "1",
    "bindings": [{"templateKey": "task-cleanup.investigator.repair", "fragmentSlot": "repairDirective"}],
}
BODY: dict[str, Any] = {"profile": "local", "manifest": [ENTRY]}

PROMPT_BODY: dict[str, Any] = {
    "name": "system",
    "agentName": "task-cleanup",
    "language": "en",
    "version": {
        "semanticVersion": "v3",
        "content": "You repair suggestions.",
        "toolContractVersion": "1",
        "outputSchemaVersion": "1",
    },
}


class SingleSql:
    """테스트 하나가 쓰는 메모리 원장을 등록 창구에 그대로 빌려 준다."""

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
    yield ledger
    ledger.close()


@pytest.fixture
def client(store: SqliteLedgerSql) -> Iterator[TestClient]:
    with TestClient(app_module.create_app()) as test_client:
        test_client.app.state.execution_sql = SingleSql(store)
        yield test_client


def _seed_edited_fragment(store: SqliteLedgerSql, content: str) -> None:
    """사람이 데이터베이스에서 고쳐 둔 판과 그것을 가리키는 채널을 미리 심는다."""
    now = datetime(2026, 7, 30, tzinfo=UTC)
    store.seed(
        "prompt_fragment_definitions",
        [
            {
                "id": "definition-1",
                "definition_key": ENTRY["definitionKey"],
                "agent_name": ENTRY["agentName"],
                "backend": ENTRY["backend"],
                "language": ENTRY["language"],
                "fragment_name": ENTRY["fragmentName"],
                "code_name": ENTRY["codeName"],
                "created_at": now,
            }
        ],
    )
    store.seed(
        "prompt_fragment_versions",
        [
            {
                "id": "version-edited",
                "definition_id": "definition-1",
                "semantic_version": "v2",
                "content": content,
                "content_hash": fragment_content_hash(content),
                "placeholders": ["taskId"],
                "tool_contract_version": "1",
                "output_schema_version": "1",
                "origin": "database-authored",
                "created_by": "local",
                "created_at": now,
            }
        ],
    )
    store.seed(
        "prompt_fragment_channels",
        [
            {
                "id": "channel-1",
                "definition_id": "definition-1",
                "channel": "staging",
                "version_id": "version-edited",
                "updated_at": now,
            }
        ],
    )


def test_조각_등록이_정의와_판과_자리와_채널을_세운다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.post(FRAGMENTS_PATH, json=BODY)

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    resolved = body["data"][0]
    assert resolved["templateKey"] == "task-cleanup.investigator.repair"
    assert resolved["fragmentSlot"] == "repairDirective"
    assert resolved["content"] == CONTENT
    assert resolved["contentHash"] == fragment_content_hash(CONTENT)
    assert resolved["placeholders"] == ["taskId"]
    assert resolved["source"] == "code-default"
    assert store.rows("prompt_fragment_definitions")[0]["definition_key"] == ENTRY["definitionKey"]
    assert store.rows("prompt_fragment_versions")[0]["origin"] == "code-default"
    assert store.rows("prompt_fragment_bindings")[0]["code_default_version"] == "v1"
    assert store.rows("prompt_fragment_channels")[0]["channel"] == "staging"


def test_두_번째_부팅이_같은_행을_다시_심지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    client.post(FRAGMENTS_PATH, json=BODY)

    res = client.post(FRAGMENTS_PATH, json=BODY)

    assert res.status_code == 200
    assert len(store.rows("prompt_fragment_definitions")) == 1
    assert len(store.rows("prompt_fragment_versions")) == 1
    assert len(store.rows("prompt_fragment_bindings")) == 1
    assert len(store.rows("prompt_fragment_channels")) == 1


def test_데이터베이스가_가진_판을_파일_값이_덮지_않는다(client: TestClient, store: SqliteLedgerSql) -> None:
    edited = "Keep the ${taskId} and nothing else."
    _seed_edited_fragment(store, edited)

    resolved = client.post(FRAGMENTS_PATH, json=BODY).json()["data"][0]

    assert resolved["content"] == edited
    assert resolved["semanticVersion"] == "v2"
    assert resolved["source"] == "database-override"
    assert store.rows("prompt_fragment_versions")[0]["content"] == edited


def test_prd_배포가_production_채널에_판을_심는다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.post(FRAGMENTS_PATH, json={**BODY, "profile": "prd"})

    assert res.status_code == 200
    assert store.rows("prompt_fragment_channels")[0]["channel"] == "production"


def test_두_축이_같은_자리를_올려도_정의와_자리가_축마다_남는다(
    client: TestClient, store: SqliteLedgerSql
) -> None:
    other = {**ENTRY, "backend": "claude-sdk", "defaultContent": "다른 축의 판"}

    client.post(FRAGMENTS_PATH, json=BODY)
    resolved = client.post(FRAGMENTS_PATH, json={**BODY, "manifest": [other]}).json()["data"][0]

    assert resolved["content"] == "다른 축의 판"
    assert len(store.rows("prompt_fragment_definitions")) == 2
    assert len(store.rows("prompt_fragment_bindings")) == 2
    assert [row["backend"] for row in store.rows("prompt_fragment_bindings")] == ["python", "claude-sdk"]


def test_선언되지_않은_프로파일이면_조각을_해석하지_않는다(client: TestClient) -> None:
    with pytest.raises(ValueError, match="unknown-profile"):
        client.post(FRAGMENTS_PATH, json={**BODY, "profile": "unknown"})


def test_본문이_스키마를_어기면_400_오류_봉투를_낸다(client: TestClient) -> None:
    res = client.post(FRAGMENTS_PATH, json={"profile": "", "manifest": []})

    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Invalid request"
    assert error["details"]


def test_프롬프트_등록이_201과_정의와_판과_채널을_낸다(client: TestClient, store: SqliteLedgerSql) -> None:
    res = client.post(REGISTER_PATH, json=PROMPT_BODY)

    assert res.status_code == 201
    data = res.json()["data"]
    assert data["definition"]["backend"] == "python"
    assert data["definition"]["name"] == "system"
    assert data["version"]["semanticVersion"] == "v3"
    assert data["version"]["contentHash"] == fragment_content_hash(PROMPT_BODY["version"]["content"])
    assert data["channel"]["channel"] == "production"
    assert data["channel"]["versionId"] == data["version"]["id"]
    assert store.rows("prompt_definitions")[0]["user_id"] == "local"


def test_프롬프트_등록이_자기신고_사용자로_정의를_나눈다(client: TestClient, store: SqliteLedgerSql) -> None:
    client.post(REGISTER_PATH, json=PROMPT_BODY)

    client.post(REGISTER_PATH, json=PROMPT_BODY, headers={"x-monitor-user": "u2"})

    assert [row["user_id"] for row in store.rows("prompt_definitions")] == ["local", "u2"]


def test_프롬프트_등록의_본문이_스키마를_어기면_400_오류_봉투를_낸다(client: TestClient) -> None:
    res = client.post(REGISTER_PATH, json={"name": "system"})

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
