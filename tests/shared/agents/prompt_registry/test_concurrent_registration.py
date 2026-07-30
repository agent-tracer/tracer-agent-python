"""워커 셋이 같은 조각을 동시에 올리는 부팅에서 등록이 성립하는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.support.sqlite_ledger import SqliteLedgerSql

from tracer_agent.shared.agents.prompt_registry.fragments import PromptFragmentRegistration
from tracer_agent.shared.agents.prompt_registry.models import RegisterAndResolveFragmentsPayload
from tracer_agent.shared.agents.runtime.ledger import LedgerSql, SqlRow

NOW = datetime(2026, 7, 30, tzinfo=UTC)
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
PAYLOAD = RegisterAndResolveFragmentsPayload.model_validate({"profile": "prd", "manifest": [ENTRY]})
WINNER_DEFINITION_ID = "definition-winner"


class LosingBoot:
    """조회와 삽입 사이에 다른 워커가 먼저 정의를 심어 삽입이 아무 행도 내지 못하는 부팅이다."""

    def __init__(self, store: SqliteLedgerSql) -> None:
        self._store = store
        self._raced = False

    def transaction(self) -> AbstractAsyncContextManager[None]:
        return self._store.transaction()

    async def fetch(self, sql: str, *args: Any) -> list[SqlRow]:
        if not self._raced and "INSERT INTO prompt_fragment_definitions" in sql:
            self._raced = True
            self._store.seed(
                "prompt_fragment_definitions",
                [
                    {
                        "id": WINNER_DEFINITION_ID,
                        "definition_key": ENTRY["definitionKey"],
                        "agent_name": ENTRY["agentName"],
                        "backend": ENTRY["backend"],
                        "language": ENTRY["language"],
                        "fragment_name": ENTRY["fragmentName"],
                        "code_name": ENTRY["codeName"],
                        "created_at": NOW,
                    }
                ],
            )
        return await self._store.fetch(sql, *args)


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


async def test_같은_묶음을_두_번_등록해도_행이_늘지_않는다(store: SqliteLedgerSql) -> None:
    registration = PromptFragmentRegistration(store)

    first = await registration.register_and_resolve(PAYLOAD, NOW)
    second = await registration.register_and_resolve(PAYLOAD, NOW)

    assert [item["versionId"] for item in first] == [item["versionId"] for item in second]
    assert len(store.rows("prompt_fragment_definitions")) == 1
    assert len(store.rows("prompt_fragment_versions")) == 1
    assert len(store.rows("prompt_fragment_bindings")) == 1
    assert len(store.rows("prompt_fragment_channels")) == 1


async def test_다른_워커가_먼저_심어도_등록이_이긴_행을_그대로_쓴다(store: SqliteLedgerSql) -> None:
    losing: LedgerSql = LosingBoot(store)

    resolved = await PromptFragmentRegistration(losing).register_and_resolve(PAYLOAD, NOW)

    assert [item["definitionId"] for item in resolved] == [WINNER_DEFINITION_ID]
    assert resolved[0]["content"] == CONTENT
    assert len(store.rows("prompt_fragment_definitions")) == 1
    assert len(store.rows("prompt_fragment_versions")) == 1
    assert len(store.rows("prompt_fragment_bindings")) == 1
    assert len(store.rows("prompt_fragment_channels")) == 1
