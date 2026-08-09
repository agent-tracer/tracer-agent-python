"""레시피와 적용 이력과 색인 아웃박스를 읽고 쓰는 문장 한 벌을 소유한다."""

from __future__ import annotations

from datetime import datetime

from ..runtime.ledger import LedgerSql, SqlRow
from ..shared.json_view import JsonValue
from .models import SEARCH_OUTBOX_TARGET_RECIPE, Recipe, RecipeApplication

_RECIPE_COLUMNS = """
id, user_id, status, title, intent, description, use_when, summary_md, request, inputs, outputs,
corrections, pitfalls, recovery, governing_rules, steps, touched_files, contributing_slices,
rationale, language, rev, parent_recipe_id, source_job_id, user_edited, last_edited_by, error,
created_at, updated_at, resolved_at, deleted_at
"""

_APPLICATION_COLUMNS = """
id, user_id, recipe_id, task_id, injected_via, outcome, note, anchor_event_id, anchor_seq, created_at
"""

_OUTBOX_COLUMNS = "id, user_id, target, target_id, attempts, last_error, created_at"

_UPSERT_RECIPE = """
INSERT INTO recipes (
    id, user_id, status, title, intent, description, use_when, summary_md, request, inputs, outputs,
    corrections, pitfalls, recovery, governing_rules, steps, touched_files, contributing_slices,
    rationale, language, rev, parent_recipe_id, source_job_id, user_edited, last_edited_by, error,
    created_at, updated_at, resolved_at, deleted_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12, $13, $14, $15, $16, $17, $18,
    $19, $20, $21, $22, $23, $24, $25, $26,
    $27, $28, $29, $30
)
ON CONFLICT (id) DO UPDATE SET
    status = EXCLUDED.status, title = EXCLUDED.title, intent = EXCLUDED.intent,
    description = EXCLUDED.description, use_when = EXCLUDED.use_when,
    summary_md = EXCLUDED.summary_md, request = EXCLUDED.request, inputs = EXCLUDED.inputs,
    outputs = EXCLUDED.outputs, corrections = EXCLUDED.corrections, pitfalls = EXCLUDED.pitfalls,
    recovery = EXCLUDED.recovery, governing_rules = EXCLUDED.governing_rules,
    steps = EXCLUDED.steps, touched_files = EXCLUDED.touched_files,
    contributing_slices = EXCLUDED.contributing_slices, rationale = EXCLUDED.rationale,
    language = EXCLUDED.language, rev = EXCLUDED.rev,
    parent_recipe_id = EXCLUDED.parent_recipe_id, source_job_id = EXCLUDED.source_job_id,
    user_edited = EXCLUDED.user_edited, last_edited_by = EXCLUDED.last_edited_by,
    error = EXCLUDED.error, updated_at = EXCLUDED.updated_at, resolved_at = EXCLUDED.resolved_at,
    deleted_at = EXCLUDED.deleted_at
"""

_UPSERT_APPLICATION = """
INSERT INTO recipe_applications (
    id, user_id, recipe_id, task_id, injected_via, outcome, note, anchor_event_id, anchor_seq, created_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (id) DO UPDATE SET
    injected_via = EXCLUDED.injected_via, outcome = EXCLUDED.outcome, note = EXCLUDED.note,
    anchor_event_id = EXCLUDED.anchor_event_id, anchor_seq = EXCLUDED.anchor_seq
"""

_INSERT_OUTBOX = """
INSERT INTO search_outbox (id, user_id, target, target_id, attempts, last_error, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (id) DO NOTHING
"""

_SELECT_RECIPE_BY_ID = f"""
SELECT {_RECIPE_COLUMNS} FROM recipes
 WHERE id = $1 AND deleted_at IS NULL
"""

_SELECT_RECIPES_BY_STATUS = f"""
SELECT {_RECIPE_COLUMNS} FROM recipes
 WHERE user_id = $1 AND status = $2 AND deleted_at IS NULL
 ORDER BY updated_at DESC
"""

_SELECT_RECIPE_IDS_BY_SOURCE_JOB = """
SELECT id FROM recipes WHERE user_id = $1 AND source_job_id = $2
"""

_SELECT_APPLICATIONS_BY_RECIPE = f"""
SELECT {_APPLICATION_COLUMNS} FROM recipe_applications
 WHERE recipe_id = $1
 ORDER BY created_at DESC
"""

_SELECT_APPLICATIONS_BY_TASK = f"""
SELECT {_APPLICATION_COLUMNS} FROM recipe_applications
 WHERE task_id = $1
"""

_SELECT_OUTBOX_BATCH = f"""
SELECT {_OUTBOX_COLUMNS} FROM search_outbox
 ORDER BY created_at ASC
 LIMIT $1
"""

_DELETE_OUTBOX = "DELETE FROM search_outbox WHERE id = $1 RETURNING id"

_MARK_OUTBOX_FAILED = """
UPDATE search_outbox SET attempts = $2, last_error = $3
 WHERE id = $1
 RETURNING id
"""


def _json_list(value: JsonValue) -> list[JsonValue]:
    """jsonb 칸이 목록이 아니면 빈 목록으로 읽는다."""
    return list(value) if isinstance(value, list) else []


def to_recipe(row: SqlRow) -> Recipe:
    """원장 행 하나를 레시피 모델로 읽는다."""
    return Recipe(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        status=str(row["status"]),
        title=str(row["title"]),
        intent=str(row["intent"]),
        description=str(row["description"]),
        summary_md=str(row["summary_md"]),
        request=str(row["request"]),
        rev=int(row["rev"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        use_when=_json_list(row["use_when"]),
        inputs=_json_list(row["inputs"]),
        outputs=_json_list(row["outputs"]),
        corrections=_json_list(row["corrections"]),
        pitfalls=_json_list(row["pitfalls"]),
        recovery=_json_list(row["recovery"]),
        governing_rules=_json_list(row["governing_rules"]),
        steps=_json_list(row["steps"]),
        touched_files=_json_list(row["touched_files"]),
        contributing_slices=_json_list(row["contributing_slices"]),
        rationale=row["rationale"],
        language=row["language"],
        parent_recipe_id=row["parent_recipe_id"],
        source_job_id=row["source_job_id"],
        user_edited=bool(row["user_edited"]),
        last_edited_by=str(row["last_edited_by"]),
        error=row["error"],
        resolved_at=row["resolved_at"],
        deleted_at=row["deleted_at"],
    )


def to_application(row: SqlRow) -> RecipeApplication:
    """원장 행 하나를 적용 이력 모델로 읽는다."""
    seq = row["anchor_seq"]
    return RecipeApplication(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        recipe_id=str(row["recipe_id"]),
        task_id=str(row["task_id"]),
        injected_via=str(row["injected_via"]),
        created_at=row["created_at"],
        outcome=row["outcome"],
        note=row["note"],
        anchor_event_id=row["anchor_event_id"],
        anchor_seq=None if seq is None else int(seq),
    )


class RecipeStore:
    """레시피 원장의 조회와 저장을 제공한다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def find_by_id(self, recipe_id: str) -> Recipe | None:
        """레시피 하나를 읽으며 지운 행은 없는 것으로 낸다."""
        rows = await self._sql.fetch(_SELECT_RECIPE_BY_ID, recipe_id)
        return to_recipe(rows[0]) if rows else None

    async def find_by_status(self, user_id: str, status: str) -> list[Recipe]:
        """그 사용자의 그 상태 레시피를 갱신 시각의 내림차순으로 읽는다."""
        rows = await self._sql.fetch(_SELECT_RECIPES_BY_STATUS, user_id, status)
        return [to_recipe(row) for row in rows]

    async def count_by_source_job(self, user_id: str, source_job_id: str) -> int:
        """그 잡이 적어 둔 후보의 수이며 지운 후보도 세어 재시도가 후보를 두 벌 만들지 않게 한다."""
        rows = await self._sql.fetch(_SELECT_RECIPE_IDS_BY_SOURCE_JOB, user_id, source_job_id)
        return len(rows)

    async def upsert(self, recipe: Recipe) -> None:
        """레시피 한 행을 적거나 같은 식별자의 행을 덮는다."""
        await self._sql.fetch(
            _UPSERT_RECIPE,
            recipe.id,
            recipe.user_id,
            recipe.status,
            recipe.title,
            recipe.intent,
            recipe.description,
            recipe.use_when,
            recipe.summary_md,
            recipe.request,
            recipe.inputs,
            recipe.outputs,
            recipe.corrections,
            recipe.pitfalls,
            recipe.recovery,
            recipe.governing_rules,
            recipe.steps,
            recipe.touched_files,
            recipe.contributing_slices,
            recipe.rationale,
            recipe.language,
            recipe.rev,
            recipe.parent_recipe_id,
            recipe.source_job_id,
            recipe.user_edited,
            recipe.last_edited_by,
            recipe.error,
            recipe.created_at,
            recipe.updated_at,
            recipe.resolved_at,
            recipe.deleted_at,
        )


class RecipeApplicationStore:
    """레시피 적용 이력의 조회와 저장을 제공한다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def find_by_recipe(self, recipe_id: str) -> list[RecipeApplication]:
        """그 레시피의 적용 이력을 만든 시각의 내림차순으로 읽는다."""
        rows = await self._sql.fetch(_SELECT_APPLICATIONS_BY_RECIPE, recipe_id)
        return [to_application(row) for row in rows]

    async def find_by_task(self, task_id: str) -> list[RecipeApplication]:
        """그 태스크에 남은 적용 이력을 읽는다."""
        rows = await self._sql.fetch(_SELECT_APPLICATIONS_BY_TASK, task_id)
        return [to_application(row) for row in rows]

    async def upsert(self, application: RecipeApplication) -> None:
        """적용 이력 한 행을 적거나 같은 식별자의 행을 덮는다."""
        await self._sql.fetch(
            _UPSERT_APPLICATION,
            application.id,
            application.user_id,
            application.recipe_id,
            application.task_id,
            application.injected_via,
            application.outcome,
            application.note,
            application.anchor_event_id,
            application.anchor_seq,
            application.created_at,
        )


class SearchOutboxStore:
    """색인 반영 요청을 적재하고 배출기가 읽고 지우는 자리를 갖는다."""

    def __init__(self, sql: LedgerSql) -> None:
        self._sql = sql

    async def enqueue(self, row_id: str, user_id: str, recipe_id: str, now: datetime) -> None:
        """레시피 쓰기와 같은 커밋에 색인 반영 요청 한 줄을 남긴다."""
        await self._sql.fetch(
            _INSERT_OUTBOX, row_id, user_id, SEARCH_OUTBOX_TARGET_RECIPE, recipe_id, 0, None, now
        )

    async def find_batch(self, limit: int) -> list[SqlRow]:
        """오래된 것부터 배출해 색인이 원장의 순서를 따라가게 한다."""
        return await self._sql.fetch(_SELECT_OUTBOX_BATCH, limit)

    async def delete(self, row_id: str) -> None:
        """색인에 반영한 행을 지운다."""
        await self._sql.fetch(_DELETE_OUTBOX, row_id)

    async def mark_failed(self, row_id: str, attempts: int, error: str) -> None:
        """실패한 행은 남겨 다음 주기가 다시 가져가며 사유를 적어 반복 실패를 관측하게 한다."""
        await self._sql.fetch(_MARK_OUTBOX_FAILED, row_id, attempts, error)
