"""recipe-scan이 낸 후보를 레시피 창구로 배달한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..runtime.outputs import AGENT_AUTHOR, object_items, post_output
from ..runtime.tracer_client import TracerApiPort

RECIPES_PATH = "/api/v1/recipes"

# 창구가 한 번에 받는 항목 수이며 계약이 그 상한을 적는다.
MAX_RECIPES = 20

# 왼쪽은 모델 출력의 snake_case 칸이고 오른쪽은 창구의 draft가 받는 camelCase 칸이다.
_DRAFT_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "title"),
    ("intent", "intent"),
    ("description", "description"),
    ("summary_md", "summaryMd"),
    ("request", "request"),
    ("rationale", "rationale"),
    ("use_when", "useWhen"),
    ("inputs", "inputs"),
    ("outputs", "outputs"),
    ("corrections", "corrections"),
    ("pitfalls", "pitfalls"),
    ("recovery", "recovery"),
    ("governing_rules", "governingRules"),
    ("steps", "steps"),
    ("touched_files", "touchedFiles"),
    ("contributing_slices", "contributingSlices"),
)
# 모델은 고쳐 쓸 레시피를 revises_recipe_id로 적고 창구는 그것을 부모 레시피로 받는다.
_REVISES_FIELD = "revises_recipe_id"

# 요청이 언어를 정하지 않은 실행의 표시이며 정본 축도 이 글자를 그대로 싣는다.
DEFAULT_LANGUAGE = "auto"


async def deliver_recipes(
    tracer: TracerApiPort, execution_id: str, data: dict[str, Any], language: str = DEFAULT_LANGUAGE
) -> bool:
    """스캔이 세운 레시피 후보를 창구로 보내며 후보가 없으면 부르지 않는다."""
    recipes = object_items(data, "recipes")
    if not recipes:
        return True
    revisions = _recipe_revisions(data)
    body = {
        "recipes": [_to_draft(recipe, revisions, language) for recipe in recipes[:MAX_RECIPES]],
        "author": AGENT_AUTHOR,
        "sourceJobId": execution_id,
    }
    return await post_output(tracer, RECIPES_PATH, body, execution_id)


def _to_draft(candidate: Mapping[str, Any], revisions: Mapping[str, Any], language: str) -> dict[str, Any]:
    """후보 하나를 창구가 받는 draft로 옮기며 창구가 갖지 않는 칸은 싣지 않는다."""
    draft: dict[str, Any] = {wire: candidate[name] for name, wire in _DRAFT_FIELDS if name in candidate}
    # 언어는 후보가 아니라 이 실행이 갖는 값이라 배치에서 받아 초안마다 싣는다.
    draft["language"] = language
    parent_id = candidate.get(_REVISES_FIELD)
    seen_rev = revisions.get(parent_id) if isinstance(parent_id, str) else None
    if seen_rev is not None:
        # 창구는 부르는 쪽이 본 판과 지금 판이 다르면 부모를 비우므로 둘을 함께 적거나 함께 뺀다.
        draft["parentRecipeId"] = parent_id
        draft["parentRecipeSeenRev"] = seen_rev
    return draft


def _recipe_revisions(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """이 실행이 검색으로 본 레시피의 판이며 근거 장부가 없으면 본 것이 없다."""
    provenance = data.get("provenance")
    if not isinstance(provenance, Mapping):
        return {}
    revisions = provenance.get("recipeRevs")
    return revisions if isinstance(revisions, Mapping) else {}
