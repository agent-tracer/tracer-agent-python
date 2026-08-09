"""사건 하나가 적용 이력 행이 되는 규칙과 넘기는 자리를 검증한다(브로커 없음)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.support.contract import conformance_case
from tracer_agent.shared.agents.recipe.projection import (
    RECIPE_INJECTED_EVENT_KIND,
    RecipeProjection,
    parse_ledger_record,
)
from tracer_agent.shared.agents.recipe.store import RecipeApplicationStore
from tracer_agent.shared.agents.recipe.topic import (
    ledger_events_consumer_group,
    ledger_events_topic,
)
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql

_CASE = conformance_case("recipe.projection")

OCCURRED_AT = "2026-01-01T00:00:00.000Z"


@pytest.fixture
def store() -> Iterator[SqliteLedgerSql]:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


def _event(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "event-1",
        "seq": "42",
        "user_id": "user-1",
        "task_id": "task-1",
        "kind": RECIPE_INJECTED_EVENT_KIND,
        "occurred_at": OCCURRED_AT,
        "payload": {"applicationId": "a1", "recipeId": "r1"},
    }
    record.update(overrides)
    return record


async def _project(store: SqliteLedgerSql, **overrides: Any) -> None:
    record = parse_ledger_record(_event(**overrides))
    assert record is not None
    await RecipeProjection(RecipeApplicationStore(store)).handle(record)


def test_토픽과_소비자_그룹과_종류를_계약에서_읽는다() -> None:
    assert ledger_events_topic() == _CASE["source"]["topic"]
    assert ledger_events_consumer_group() == _CASE["source"]["consumerGroup"]
    assert _CASE["selection"]["kind"] == RECIPE_INJECTED_EVENT_KIND


async def test_사건의_칸이_계약이_적은_열로_간다(store: SqliteLedgerSql) -> None:
    await _project(store)

    row = store.rows("recipe_applications")[0]
    assert row["id"] == "a1"
    assert row["user_id"] == "user-1"
    assert row["recipe_id"] == "r1"
    assert row["task_id"] == "task-1"
    assert row["injected_via"] == "pull"
    assert row["outcome"] is None
    assert row["note"] is None
    assert row["anchor_event_id"] == "event-1"
    assert row["anchor_seq"] == 42


async def test_사건이_실은_주입_경로를_그대로_적는다(store: SqliteLedgerSql) -> None:
    await _project(store, payload={"applicationId": "a1", "recipeId": "r1", "injectedVia": "manual"})

    assert store.rows("recipe_applications")[0]["injected_via"] == "manual"


async def test_고르지_않은_종류는_아무것도_하지_않는다(store: SqliteLedgerSql) -> None:
    await _project(store, kind="agent_tracer.user.message")

    assert store.rows("recipe_applications") == []


@pytest.mark.parametrize(
    "payload", [{"recipeId": "r1"}, {"applicationId": "a1"}, {}], ids=["no-id", "no-recipe", "empty"]
)
async def test_행의_식별자나_대상이_없으면_넘긴다(store: SqliteLedgerSql, payload: dict[str, Any]) -> None:
    await _project(store, payload=payload)

    assert store.rows("recipe_applications") == []


async def test_같은_태스크의_같은_레시피는_행을_늘리지_않는다(store: SqliteLedgerSql) -> None:
    await _project(store)

    await _project(store, id="event-2", payload={"applicationId": "a2", "recipeId": "r1"})

    assert [row["id"] for row in store.rows("recipe_applications")] == ["a1"]


async def test_같은_사건이_다시_와도_행이_늘지_않는다(store: SqliteLedgerSql) -> None:
    await _project(store)
    await _project(store)

    assert len(store.rows("recipe_applications")) == 1


async def test_같은_태스크의_다른_레시피는_행을_따로_만든다(store: SqliteLedgerSql) -> None:
    await _project(store)

    await _project(store, id="event-2", payload={"applicationId": "a2", "recipeId": "r2"})

    assert [row["id"] for row in store.rows("recipe_applications")] == ["a1", "a2"]


class Test사건_읽기:
    def test_payload_는_JSON_문자열로_실려_올_수_있다(self) -> None:
        record = parse_ledger_record(_event(payload='{"applicationId": "a1", "recipeId": "r1"}'))

        assert record is not None
        assert record.payload["recipeId"] == "r1"

    def test_일련번호는_정수로_실려_올_수_있다(self) -> None:
        record = parse_ledger_record(_event(seq=42))

        assert record is not None
        assert record.seq == 42

    @pytest.mark.parametrize("field", ["id", "user_id", "task_id", "kind", "occurred_at"])
    def test_필수_칸이_없으면_읽지_못한_것으로_본다(self, field: str) -> None:
        broken = _event()
        broken.pop(field)

        assert parse_ledger_record(broken) is None

    def test_시각을_읽지_못하면_읽지_못한_것으로_본다(self) -> None:
        assert parse_ledger_record(_event(occurred_at="어제")) is None
