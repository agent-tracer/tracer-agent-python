"""종결한 스캔의 후보가 자기 원장의 레시피 표와 색인 아웃박스에 서는지 검증한다(네트워크 없음)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests.support.chat_surface import SingleSql
from tests.support.contract import conformance_case
from tracer_agent.shared.agents.recipe_scan.models import (
    Language,
    ProvenanceWire,
    RecipeCandidate,
    RecipeScanResult,
)
from tracer_agent.shared.agents.runtime.__fakes__.sqlite_ledger import SqliteLedgerSql
from tracer_agent.worker.agents.recipe_scan.outputs import write_recipes
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.job_agent import dumped
from tracer_agent.worker.agents.runtime.outputs import JobOutputTargets
from tracer_agent.worker.agents.shared.empty_result import DEGRADED

_DRAFT = conformance_case("recipe.ledger")["ledgerWrite"]["drafts"]["recipe"]
_DERIVED = conformance_case("recipe.ledger")["ledgerWrite"]["derived"]["recipe"]

USER = "user-1"


def _scan_result(**overrides: Any) -> dict[str, Any]:
    """스캔이 종결 창구에 내는 산출물이며 후보는 실제 모델을 거쳐 만든다."""
    values: dict[str, Any] = {
        "title": "Add a safe migration",
        "intent": "스키마 변경을 안전하게 넣는다",
        "description": "저장된 모양이 바뀔 때 쓴다.",
        "use_when": ["저장된 엔터티의 모양이 바뀐다"],
        "summary_md": "- 변경을 정의한다",
        "request": "사용자가 마이그레이션 추가를 요청했다.",
        "inputs": ["요청이 말한 엔터티 변경"],
        "outputs": ["정방향 마이그레이션과 되돌리기"],
        "corrections": [
            {
                "whatAgentDid": "평범한 편집으로 다뤘다",
                "howCorrected": "마이그레이션을 등록했다",
                "evidence": ["event-1"],
            }
        ],
        "pitfalls": [
            {
                "pitfall": "재시도가 순번을 다시 쓴다",
                "whyNonObvious": "한 번만 돌면 겹치지 않는다",
                "evidence": ["event-1"],
            }
        ],
        "recovery": [
            {
                "symptom": "되돌리기가 이전 스키마를 남겼다",
                "action": "down 경로를 고치고 다시 돌렸다",
                "evidence": ["event-2"],
                "stepOrder": 1,
            }
        ],
        "governing_rules": ["rule-1"],
        "revises_recipe_id": "recipe-1",
        "steps": [{"order": 1, "action": "마이그레이션을 등록한다", "evidence": ["event-1"]}],
        "touched_files": [{"path": "docs/db.md", "role": "read", "why": "명명 규칙", "loadWhen": "작성 전"}],
        "contributing_slices": [{"taskId": "task-1", "turnIds": ["turn-1"], "eventIds": ["event-1"]}],
        "rationale": "반복 가능한 절차다.",
    }
    values.update(overrides)
    result = RecipeScanResult(
        recipes=[RecipeCandidate.model_validate(values)],
        provenance=ProvenanceWire(recipeRevs={"recipe-1": 3}),
    )
    return dumped(result, exclude_none=True)


def _targets(store: SqliteLedgerSql) -> JobOutputTargets:
    return JobOutputTargets(SingleSql(store), FakeTracerApi())


def _seed_parent(store: SqliteLedgerSql, rev: int, user_id: str = USER) -> None:
    """개정 대상이 될 부모 레시피 한 행을 원장에 세운다."""
    store.seed(
        "recipes",
        [
            {
                "id": "recipe-1",
                "user_id": user_id,
                "status": "active",
                "title": "부모",
                "intent": "부모 의도",
                "description": "부모 설명",
                "summary_md": "- 부모",
                "request": "부모 요청",
                "rev": rev,
                "created_at": "2026-01-01T00:00:00.000000",
                "updated_at": "2026-01-01T00:00:00.000000",
            }
        ],
    )


async def _written(store: SqliteLedgerSql, **overrides: Any) -> dict[str, Any]:
    """후보 하나를 실제로 적고 원장에 남은 행을 낸다."""
    language: Language = overrides.pop("language", "ko")
    await write_recipes(_targets(store), USER, "job-draft", _scan_result(**overrides), language)
    return store.rows("recipes")[-1]


@pytest.fixture
def store() -> Any:
    ledger = SqliteLedgerSql()
    yield ledger
    ledger.close()


async def test_레시피_후보를_한_커밋으로_적고_출처_잡을_함께_적는다(store: SqliteLedgerSql) -> None:
    await write_recipes(_targets(store), USER, "job-1", {"recipes": [{"title": "하나"}, {"title": "둘"}]})

    rows = store.rows("recipes")
    assert [row["title"] for row in rows] == ["하나", "둘"]
    # 같은 잡의 재시도가 후보를 두 벌 만들지 않도록 이 값으로 멱등을 판정한다.
    assert {row["source_job_id"] for row in rows} == {"job-1"}
    assert [row["target_id"] for row in store.rows("search_outbox")] == [row["id"] for row in rows]


async def test_같은_잡이_다시_적어도_후보가_두_벌이_되지_않는다(store: SqliteLedgerSql) -> None:
    await write_recipes(_targets(store), USER, "job-2", {"recipes": [{"title": "하나"}]})
    await write_recipes(_targets(store), USER, "job-2", {"recipes": [{"title": "하나"}]})

    assert len(store.rows("recipes")) == 1
    assert len(store.rows("search_outbox")) == 1


async def test_후보가_없으면_원장을_열지_않는다(store: SqliteLedgerSql) -> None:
    await write_recipes(_targets(store), USER, "job-4", {"recipes": []})

    assert store.rows("recipes") == []


async def test_원장이_받지_않으면_종결을_되돌리지_않되_쓰기_실패를_남긴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Unwritable:
        def connect(self) -> Any:
            raise ConnectionError("agent db is down")

    with caplog.at_level(logging.ERROR):
        # 원장은 이미 닫혔으므로 산출물이 서지 않은 채로 잡은 완료로 남는다.
        written = await write_recipes(
            JobOutputTargets(Unwritable(), FakeTracerApi()),  # type: ignore[arg-type]
            USER,
            "job-7",
            {"recipes": [{"title": "하나"}]},
        )

    # 삼킨 실패는 원장에서 성공한 실행과 갈리지 않으므로 사유와 함께 남긴다.
    assert written is False
    assert any(
        f"emptyResultReason={DEGRADED}" in record.getMessage() and "job-7" in record.getMessage()
        for record in caplog.records
    )


async def test_원장이_받으면_적었다고_낸다(store: SqliteLedgerSql) -> None:
    assert await write_recipes(_targets(store), USER, "job-8", {"recipes": [{"title": "하나"}]}) is True
    assert await write_recipes(_targets(store), USER, "job-9", {"recipes": []}) is True


async def test_모델이_적은_신규_칸이_원장의_이름으로_끝까지_간다(store: SqliteLedgerSql) -> None:
    row = await _written(store)

    # 후보에만 서고 행 매핑에서 빠지면 모델이 적은 값이 저장 직전에 사라진다.
    assert row["use_when"] == ["저장된 엔터티의 모양이 바뀐다"]
    assert row["inputs"] == ["요청이 말한 엔터티 변경"]
    assert row["outputs"] == ["정방향 마이그레이션과 되돌리기"]
    assert row["recovery"] == [
        {
            "symptom": "되돌리기가 이전 스키마를 남겼다",
            "action": "down 경로를 고치고 다시 돌렸다",
            "evidence": ["event-2"],
            "stepOrder": 1,
        }
    ]
    assert row["steps"][0]["evidence"] == ["event-1"]
    assert row["touched_files"][0]["why"] == "명명 규칙"
    assert row["touched_files"][0]["loadWhen"] == "작성 전"


async def test_후보의_칸을_원장의_열_이름으로_옮긴다(store: SqliteLedgerSql) -> None:
    row = await _written(store)

    assert row["summary_md"] == "- 변경을 정의한다"
    assert row["governing_rules"] == ["rule-1"]
    assert row["touched_files"][0]["path"] == "docs/db.md"
    assert row["contributing_slices"][0]["taskId"] == "task-1"


async def test_종결_단계가_정하는_값을_모델이_아니라_이_자리가_적는다(store: SqliteLedgerSql) -> None:
    row = await _written(store)

    assert row["status"] == _DERIVED["status"]
    assert row["last_edited_by"] == _DERIVED["lastEditedBy"]
    assert row["user_edited"] == 0
    assert row["error"] is None
    assert row["resolved_at"] is None
    assert row["deleted_at"] is None
    assert row["source_job_id"] == "job-draft"


async def test_본_판과_같은_부모는_개정으로_이어_붙인다(store: SqliteLedgerSql) -> None:
    _seed_parent(store, rev=3)

    row = await _written(store)

    assert row["parent_recipe_id"] == "recipe-1"
    assert row["rev"] == 4


async def test_판이_어긋난_부모는_비운다(store: SqliteLedgerSql) -> None:
    _seed_parent(store, rev=9)

    row = await _written(store)

    assert row["parent_recipe_id"] is None
    assert row["rev"] == 1


async def test_남의_부모는_비운다(store: SqliteLedgerSql) -> None:
    _seed_parent(store, rev=3, user_id="other")

    row = await _written(store)

    assert row["parent_recipe_id"] is None
    assert row["rev"] == 1


async def test_고쳐_쓸_레시피가_없으면_부모를_비운다(store: SqliteLedgerSql) -> None:
    _seed_parent(store, rev=3)

    row = await _written(store, revises_recipe_id=None)

    assert row["parent_recipe_id"] is None
    assert row["rev"] == 1


async def test_행이_계약이_적은_칸만_쓰고_요구한_칸을_모두_싣는다(store: SqliteLedgerSql) -> None:
    row = await _written(store)
    declared = {_column(name) for name in list(_DRAFT["required"]) + list(_DRAFT["optional"])}
    written = {name for name, value in row.items() if value not in (None, "", [], 0)}

    assert written - declared <= _DERIVED_COLUMNS
    assert {_column(name) for name in _DRAFT["required"]} <= set(row)
    assert set(row["contributing_slices"][0]) == set(_DRAFT["slice"])


@pytest.mark.parametrize("language", ["ko", "auto"])
async def test_행이_이_실행의_답변_언어를_함께_적는다(store: SqliteLedgerSql, language: Language) -> None:
    # 정본 축이 auto 도 그대로 적으므로 값이 언어가 아닌 경우까지 같은 글자를 낸다.
    row = await _written(store, language=language)

    assert row["language"] == language


# 실행이 싣지 않고 종결 단계가 정하는 열이며 계약의 derived 가 그 값을 갖는다.
_DERIVED_COLUMNS = {
    "id",
    "user_id",
    "status",
    "rev",
    "language",
    "parent_recipe_id",
    "source_job_id",
    "last_edited_by",
    "created_at",
    "updated_at",
}


def _column(name: str) -> str:
    """계약이 적은 camelCase 칸 이름을 원장 열 이름으로 옮긴다."""
    mapped = {"summaryMd": "summary_md", "useWhen": "use_when", "governingRules": "governing_rules"}
    if name in mapped:
        return mapped[name]
    if name == "touchedFiles":
        return "touched_files"
    if name == "contributingSlices":
        return "contributing_slices"
    if name == "parentRecipeId":
        return "parent_recipe_id"
    if name == "parentRecipeSeenRev":
        return "rev"
    return name
