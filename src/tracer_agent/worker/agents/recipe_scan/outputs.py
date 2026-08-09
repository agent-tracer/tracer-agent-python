"""recipe-scan이 낸 후보를 자기 원장의 레시피 표와 색인 아웃박스에 적는다."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tracer_agent.shared.agents.recipe.models import (
    RECIPE_EDITOR_AGENT,
    RECIPE_STATUS_CANDIDATE,
    Recipe,
)
from tracer_agent.shared.agents.recipe.store import RecipeStore, SearchOutboxStore
from tracer_agent.shared.agents.runtime.ledger import LedgerSql
from tracer_agent.shared.agents.shared.ids import generate_ulid

from ..runtime.outputs import JobOutputTargets, object_items, write_output

RECIPE_OUTPUT_LABEL = "recipe"

# 왼쪽은 모델 출력의 snake_case 칸이고 오른쪽은 원장 행의 칸이다.
_ROW_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "title"),
    ("intent", "intent"),
    ("description", "description"),
    ("summary_md", "summary_md"),
    ("request", "request"),
    ("rationale", "rationale"),
    ("use_when", "use_when"),
    ("inputs", "inputs"),
    ("outputs", "outputs"),
    ("corrections", "corrections"),
    ("pitfalls", "pitfalls"),
    ("recovery", "recovery"),
    ("governing_rules", "governing_rules"),
    ("steps", "steps"),
    ("touched_files", "touched_files"),
    ("contributing_slices", "contributing_slices"),
)
# 모델은 고쳐 쓸 레시피를 revises_recipe_id로 적고 원장은 그것을 부모 레시피로 받는다.
_REVISES_FIELD = "revises_recipe_id"

# 요청이 언어를 정하지 않은 실행의 표시이며 정본 축도 이 글자를 그대로 적는다.
DEFAULT_LANGUAGE = "auto"


async def write_recipes(
    targets: JobOutputTargets,
    user_id: str,
    execution_id: str,
    data: dict[str, Any],
    language: str = DEFAULT_LANGUAGE,
) -> bool:
    """스캔이 세운 후보를 자기 원장에 적으며 후보가 없으면 아무것도 쓰지 않는다."""
    candidates = object_items(data, "recipes")
    if not candidates:
        return True
    revisions = _recipe_revisions(data)
    now = datetime.now(UTC)

    async def work(sql: LedgerSql) -> None:
        recipes = RecipeStore(sql)
        # 같은 실행이 여러 번 시도돼도 후보가 두 벌이 되지 않는다.
        if await recipes.count_by_source_job(user_id, execution_id) > 0:
            return
        outbox = SearchOutboxStore(sql)
        for candidate in candidates:
            parent = await _owned_parent(recipes, user_id, candidate, revisions)
            row = _to_row(generate_ulid(now), user_id, execution_id, candidate, parent, language, now)
            await recipes.upsert(row)
            await outbox.enqueue(generate_ulid(now), user_id, row.id, now)

    return await write_output(targets, RECIPE_OUTPUT_LABEL, execution_id, work)


async def _owned_parent(
    recipes: RecipeStore, user_id: str, candidate: Mapping[str, Any], revisions: Mapping[str, Any]
) -> Recipe | None:
    """부모가 이 사용자의 것이고 관측한 판을 여전히 가리킬 때만 개정으로 이어 붙인다."""
    parent_id = candidate.get(_REVISES_FIELD)
    if not isinstance(parent_id, str):
        return None
    seen_rev = revisions.get(parent_id)
    if not isinstance(seen_rev, int):
        return None
    parent = await recipes.find_by_id(parent_id)
    if parent is None or parent.user_id != user_id:
        return None
    return parent if parent.rev == seen_rev else None


def _to_row(
    row_id: str,
    user_id: str,
    execution_id: str,
    candidate: Mapping[str, Any],
    parent: Recipe | None,
    language: str,
    now: datetime,
) -> Recipe:
    """후보 하나를 원장 행으로 옮기며 식별자와 상태와 판은 이 자리가 정한다."""
    row = Recipe(
        id=row_id,
        user_id=user_id,
        status=RECIPE_STATUS_CANDIDATE,
        title="",
        intent="",
        description="",
        summary_md="",
        request="",
        rev=parent.rev + 1 if parent is not None else 1,
        created_at=now,
        updated_at=now,
        language=language,
        parent_recipe_id=parent.id if parent is not None else None,
        source_job_id=execution_id,
        user_edited=False,
        last_edited_by=RECIPE_EDITOR_AGENT,
    )
    for name, column in _ROW_FIELDS:
        if name in candidate:
            setattr(row, column, candidate[name])
    return row


def _recipe_revisions(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """이 실행이 검색으로 본 레시피의 판이며 근거 장부가 없으면 본 것이 없다."""
    provenance = data.get("provenance")
    if not isinstance(provenance, Mapping):
        return {}
    revisions = provenance.get("recipeRevs")
    return revisions if isinstance(revisions, Mapping) else {}
