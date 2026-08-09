"""레시피 원장의 조회와 상태 변경을 한 벌의 유스케이스로 소유한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..runtime.ledger import LedgerSql
from ..shared.json_view import JsonObject, JsonValue
from .models import (
    RECIPE_INJECTED_VIA_MANUAL,
    RECIPE_NOT_FOUND,
    RECIPE_STATUSES,
    Recipe,
    RecipeApplication,
    RecipeRejected,
    cited_task_ids,
    recipe_application_view,
    recipe_stats,
    recipe_view,
)
from .search import RecipeSearchPort
from .store import RecipeApplicationStore, RecipeStore, SearchOutboxStore
from .tasks import RecipeTaskReader

# 적중 수의 기본값과 상한이며 계약의 검색 색인 선언이 같은 수를 갖는다.
RECIPE_SEARCH_LIMIT = {"default": 3, "min": 1, "max": 10}


@dataclass(frozen=True)
class RecipeLedger:
    """한 연결이 여는 레시피 저장소 묶음이며 한 커밋 안에서만 함께 쓴다."""

    recipes: RecipeStore
    applications: RecipeApplicationStore
    search_outbox: SearchOutboxStore

    @staticmethod
    def open(sql: LedgerSql) -> RecipeLedger:
        """연결 하나에서 세 저장소를 세운다."""
        return RecipeLedger(RecipeStore(sql), RecipeApplicationStore(sql), SearchOutboxStore(sql))


async def owned_recipe(recipes: RecipeStore, user_id: str, recipe_id: str) -> Recipe:
    """남의 레시피는 존재 자체를 알리지 않으므로 없는 것과 같은 거절을 낸다."""
    recipe = await recipes.find_by_id(recipe_id)
    if recipe is None or recipe.user_id != user_id:
        raise RecipeRejected(RECIPE_NOT_FOUND)
    return recipe


async def list_recipes(
    ledger: RecipeLedger, tasks: RecipeTaskReader, user_id: str, status: str | None
) -> JsonObject:
    """레시피를 상태로 걸러 내고 인용된 태스크의 제목 표를 함께 낸다."""
    recipes = await _collect(ledger.recipes, user_id, status)
    items: list[JsonValue] = []
    for recipe in recipes:
        applications = await ledger.applications.find_by_recipe(recipe.id)
        items.append({**recipe_view(recipe), "stats": recipe_stats(applications).view()})
    return {"items": items, "taskTitles": await _task_titles(tasks, user_id, recipes)}


async def _collect(recipes: RecipeStore, user_id: str, status: str | None) -> list[Recipe]:
    """상태를 싣지 않으면 선언 순서로 이어 붙이고 전체를 다시 정렬하지 않는다."""
    if status is not None:
        return await recipes.find_by_status(user_id, status)
    found: list[Recipe] = []
    for declared in RECIPE_STATUSES:
        found.extend(await recipes.find_by_status(user_id, declared))
    return found


async def _task_titles(tasks: RecipeTaskReader, user_id: str, recipes: list[Recipe]) -> dict[str, JsonValue]:
    """인용된 식별자를 모아 한 번에 물어 레시피 수만큼 왕복이 생기지 않게 한다."""
    ids = cited_task_ids(recipes)
    if not ids:
        return {}
    wanted = set(ids)
    found = await tasks.titles_by_ids(user_id, ids)
    return {task_id: title for task_id, title in found.items() if task_id in wanted}


async def search_recipes(search: RecipeSearchPort, user_id: str, q: str, limit: int | None) -> JsonObject:
    """레시피를 본문 유사도로 찾으며 다듬은 질의가 비면 색인을 부르지 않는다."""
    trimmed = q.strip()
    if not trimmed:
        return {"items": []}
    hits = await search.search(user_id, trimmed, _clamp_limit(limit))
    return {
        "items": [
            {
                "recipeId": hit.id,
                "title": hit.title,
                "intent": hit.intent,
                "description": hit.description,
                "useWhen": [*hit.use_when],
                "score": hit.score,
            }
            for hit in hits
        ]
    }


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return RECIPE_SEARCH_LIMIT["default"]
    return min(max(int(limit), RECIPE_SEARCH_LIMIT["min"]), RECIPE_SEARCH_LIMIT["max"])


async def get_recipe(ledger: RecipeLedger, user_id: str, recipe_id: str) -> JsonObject:
    """레시피 하나를 전문과 적용 이력까지 낸다."""
    recipe = await owned_recipe(ledger.recipes, user_id, recipe_id)
    applications = await ledger.applications.find_by_recipe(recipe_id)
    return {
        "recipe": recipe_view(recipe),
        "stats": recipe_stats(applications).view(),
        "applications": [recipe_application_view(one) for one in applications],
    }


async def accept_recipe(
    ledger: RecipeLedger, ids: list[str], user_id: str, recipe_id: str, now: datetime
) -> JsonObject:
    """후보를 채택하고 고쳐 쓴 부모가 있으면 그 부모를 대체됨으로 함께 옮긴다."""
    recipe = await owned_recipe(ledger.recipes, user_id, recipe_id)
    parent = await _owned_parent(ledger.recipes, user_id, recipe.parent_recipe_id)
    recipe.accept(now)
    await ledger.recipes.upsert(recipe)
    await ledger.search_outbox.enqueue(ids[0], user_id, recipe.id, now)
    if parent is not None:
        parent.supersede(now)
        await ledger.recipes.upsert(parent)
        await ledger.search_outbox.enqueue(ids[1], user_id, parent.id, now)
    return {"recipe": recipe_view(recipe)}


async def _owned_parent(recipes: RecipeStore, user_id: str, parent_id: str | None) -> Recipe | None:
    """부모가 남의 것이면 거절하지 않고 부모가 없는 것으로 보아 채택만 한다."""
    if parent_id is None:
        return None
    parent = await recipes.find_by_id(parent_id)
    return parent if parent is not None and parent.user_id == user_id else None


async def dismiss_recipe(
    ledger: RecipeLedger, row_id: str, user_id: str, recipe_id: str, now: datetime
) -> JsonObject:
    """후보를 보류하고 검색 문서가 그 상태를 따라가도록 색인 반영을 함께 적재한다."""
    recipe = await owned_recipe(ledger.recipes, user_id, recipe_id)
    recipe.dismiss(now)
    await ledger.recipes.upsert(recipe)
    await ledger.search_outbox.enqueue(row_id, user_id, recipe.id, now)
    return {"recipe": recipe_view(recipe)}


async def retire_recipe(
    ledger: RecipeLedger, row_id: str, user_id: str, recipe_id: str, now: datetime
) -> JsonObject:
    """쓰이던 레시피를 폐기해 검색이 더는 그 절차를 모델에게 내지 않게 한다."""
    recipe = await owned_recipe(ledger.recipes, user_id, recipe_id)
    recipe.retire(now)
    await ledger.recipes.upsert(recipe)
    await ledger.search_outbox.enqueue(row_id, user_id, recipe.id, now)
    return {"recipe": recipe_view(recipe)}


async def edit_recipe(
    ledger: RecipeLedger,
    row_id: str,
    user_id: str,
    recipe_id: str,
    revision: dict[str, str],
    now: datetime,
) -> JsonObject:
    """채택된 레시피의 본문을 사람이 고치고 그 사실을 판과 편집 주체에 남긴다."""
    recipe = await owned_recipe(ledger.recipes, user_id, recipe_id)
    recipe.edit_by_user(revision, now)
    await ledger.recipes.upsert(recipe)
    await ledger.search_outbox.enqueue(row_id, user_id, recipe.id, now)
    return {"recipe": recipe_view(recipe)}


async def delete_recipe(
    ledger: RecipeLedger, row_id: str, user_id: str, recipe_id: str, now: datetime
) -> JsonObject:
    """보류하거나 폐기한 레시피를 지운 것으로 표시하고 검색 문서도 함께 사라지게 한다."""
    recipe = await owned_recipe(ledger.recipes, user_id, recipe_id)
    recipe.delete(now)
    await ledger.recipes.upsert(recipe)
    # 배출기가 지운 레시피를 조회로 찾지 못해 검색 문서를 지운다.
    await ledger.search_outbox.enqueue(row_id, user_id, recipe.id, now)
    return {"deleted": True, "id": recipe.id}


async def report_recipe_outcome(
    ledger: RecipeLedger,
    row_id: str,
    user_id: str,
    recipe_id: str,
    body: dict[str, str | None],
    now: datetime,
) -> JsonObject:
    """이 태스크에 열린 적용 이력이 없으면 즉석에서 하나 만들어 결과를 적는다."""
    await owned_recipe(ledger.recipes, user_id, recipe_id)
    task_id = str(body["taskId"])
    opened = await ledger.applications.find_by_task(task_id)
    existing = next((one for one in opened if one.recipe_id == recipe_id), None)
    application = existing or _manual(row_id, user_id, recipe_id, task_id, now)
    application.report_outcome(str(body["outcome"]), body.get("note"))
    await ledger.applications.upsert(application)
    return {"application": recipe_application_view(application)}


def _manual(row_id: str, user_id: str, recipe_id: str, task_id: str, now: datetime) -> RecipeApplication:
    """사건에서 오지 않은 행이므로 사건 좌표를 비운다."""
    return RecipeApplication(
        id=row_id,
        user_id=user_id,
        recipe_id=recipe_id,
        task_id=task_id,
        injected_via=RECIPE_INJECTED_VIA_MANUAL,
        created_at=now,
        outcome=None,
        note=None,
        anchor_event_id=None,
        anchor_seq=None,
    )
