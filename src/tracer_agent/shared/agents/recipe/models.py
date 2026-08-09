"""레시피 원장 행 하나의 상태 전이와 창구가 내는 칸을 소유한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..shared.instant import iso, opt_iso
from ..shared.json_view import JsonObject, JsonValue

RECIPE_STATUS_CANDIDATE = "candidate"
RECIPE_STATUS_ACTIVE = "active"
RECIPE_STATUS_DISMISSED = "dismissed"
RECIPE_STATUS_SUPERSEDED = "superseded"
RECIPE_STATUS_RETIRED = "retired"

# 목록 창구가 상태를 싣지 않았을 때 결과를 이어 붙이는 순서다.
RECIPE_STATUSES = (
    RECIPE_STATUS_CANDIDATE,
    RECIPE_STATUS_ACTIVE,
    RECIPE_STATUS_DISMISSED,
    RECIPE_STATUS_SUPERSEDED,
    RECIPE_STATUS_RETIRED,
)

RECIPE_OUTCOME_COMPLETED = "completed"
RECIPE_OUTCOMES = (RECIPE_OUTCOME_COMPLETED, "abandoned", "superseded")

RECIPE_EDITOR_AGENT = "agent"
RECIPE_EDITOR_USER = "user"

RECIPE_INJECTED_VIA_PULL = "pull"
RECIPE_INJECTED_VIA_MANUAL = "manual"

# 이 원장이 소유한 색인 대상은 레시피 하나이며 migration 의 CHECK 가 같은 값을 받는다.
SEARCH_OUTBOX_TARGET_RECIPE = "recipe"

RECIPE_NOT_FOUND = (404, "not_found", "Recipe not found")
RECIPE_NOT_CANDIDATE = (409, "recipe.not-candidate", "Recipe is not a candidate")
RECIPE_NOT_ACTIVE = (409, "recipe.not-active", "Recipe is not active")
RECIPE_NOT_DELETABLE = (400, "recipe.not-deletable", "Recipe is not deletable")


class RecipeRejected(Exception):
    """레시피 창구가 요청을 받아들일 수 없어 계약이 정한 상태와 코드로 돌려보낸다."""

    def __init__(self, rejection: tuple[int, str, str]) -> None:
        status, code, message = rejection
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Recipe:
    """레시피 원장 행 하나의 상태와 본문을 들고 상태 전이의 조건을 지킨다."""

    id: str
    user_id: str
    status: str
    title: str
    intent: str
    description: str
    summary_md: str
    request: str
    rev: int
    created_at: datetime
    updated_at: datetime
    use_when: list[JsonValue] = field(default_factory=list)
    inputs: list[JsonValue] = field(default_factory=list)
    outputs: list[JsonValue] = field(default_factory=list)
    corrections: list[JsonValue] = field(default_factory=list)
    pitfalls: list[JsonValue] = field(default_factory=list)
    recovery: list[JsonValue] = field(default_factory=list)
    governing_rules: list[JsonValue] = field(default_factory=list)
    steps: list[JsonValue] = field(default_factory=list)
    touched_files: list[JsonValue] = field(default_factory=list)
    contributing_slices: list[JsonValue] = field(default_factory=list)
    rationale: str | None = None
    language: str | None = None
    parent_recipe_id: str | None = None
    source_job_id: str | None = None
    user_edited: bool = False
    last_edited_by: str = RECIPE_EDITOR_AGENT
    error: str | None = None
    resolved_at: datetime | None = None
    deleted_at: datetime | None = None

    def accept(self, now: datetime) -> None:
        """후보를 채택하고 갱신 시각과 해소 시각을 함께 적는다."""
        if self.status != RECIPE_STATUS_CANDIDATE:
            raise RecipeRejected(RECIPE_NOT_CANDIDATE)
        self.status = RECIPE_STATUS_ACTIVE
        self.updated_at = now
        self.resolved_at = now

    def dismiss(self, now: datetime) -> None:
        """후보를 보류하고 갱신 시각과 해소 시각을 함께 적는다."""
        if self.status != RECIPE_STATUS_CANDIDATE:
            raise RecipeRejected(RECIPE_NOT_CANDIDATE)
        self.status = RECIPE_STATUS_DISMISSED
        self.updated_at = now
        self.resolved_at = now

    def retire(self, now: datetime) -> None:
        """폐기는 채택 시점에 적은 해소 시각을 그대로 둔다."""
        if self.status != RECIPE_STATUS_ACTIVE:
            raise RecipeRejected(RECIPE_NOT_ACTIVE)
        self.status = RECIPE_STATUS_RETIRED
        self.updated_at = now

    def supersede(self, now: datetime) -> None:
        """자식 후보를 채택할 때만 일어나므로 이 전이는 상태를 먼저 보지 않는다."""
        self.status = RECIPE_STATUS_SUPERSEDED
        self.updated_at = now
        self.resolved_at = now

    def can_delete(self) -> bool:
        """보류하거나 폐기한 레시피만 지울 수 있다."""
        return self.status in (RECIPE_STATUS_DISMISSED, RECIPE_STATUS_RETIRED)

    def delete(self, now: datetime) -> None:
        """행을 지우지 않고 지운 시각만 적으므로 적용 이력이 가리키는 대상이 사라지지 않는다."""
        if not self.can_delete():
            raise RecipeRejected(RECIPE_NOT_DELETABLE)
        self.deleted_at = now
        self.updated_at = now

    def edit_by_user(self, revision: dict[str, str], now: datetime) -> None:
        """채택된 레시피에서 실은 칸만 고치고 판과 편집 주체를 함께 옮긴다."""
        if self.status != RECIPE_STATUS_ACTIVE:
            raise RecipeRejected(RECIPE_NOT_ACTIVE)
        for name in ("title", "intent", "description", "summary_md"):
            value = revision.get(name)
            if value is not None:
                setattr(self, name, value)
        self.user_edited = True
        self.last_edited_by = RECIPE_EDITOR_USER
        self.rev += 1
        self.updated_at = now


@dataclass
class RecipeApplication:
    """레시피 하나가 태스크 하나에 쓰인 이력이며 결과는 에이전트의 자기보고로 채워진다."""

    id: str
    user_id: str
    recipe_id: str
    task_id: str
    injected_via: str
    created_at: datetime
    outcome: str | None = None
    note: str | None = None
    # 이 행을 만든 사건이며 자기보고가 즉석에서 만든 행은 사건이 없어 비어 있다.
    anchor_event_id: str | None = None
    anchor_seq: int | None = None

    def report_outcome(self, outcome: str, note: str | None) -> None:
        """다시 보고하면 앞의 결과를 덮는다."""
        self.outcome = outcome
        self.note = note


@dataclass(frozen=True)
class RecipeStats:
    """적용 이력을 집계한 값이며 자기보고가 붙지 않은 적용은 성공률의 분모에 들지 않는다."""

    application_count: int
    decided_count: int
    success_rate: float

    def view(self) -> JsonObject:
        """통계를 창구가 내는 칸으로 낸다."""
        return {
            "applicationCount": self.application_count,
            "decidedCount": self.decided_count,
            "successRate": self.success_rate,
        }


def recipe_stats(applications: list[RecipeApplication]) -> RecipeStats:
    """적용 이력에서 총 수와 자기보고가 붙은 수와 성공률을 낸다."""
    decided = [one for one in applications if one.outcome is not None]
    succeeded = [one for one in decided if one.outcome == RECIPE_OUTCOME_COMPLETED]
    return RecipeStats(
        application_count=len(applications),
        decided_count=len(decided),
        success_rate=len(succeeded) / len(decided) if decided else 0,
    )


def recipe_view(recipe: Recipe) -> JsonObject:
    """조회와 상태 변경이 모두 내는 레시피 한 건의 칸이다."""
    return {
        "id": recipe.id,
        "userId": recipe.user_id,
        "status": recipe.status,
        "title": recipe.title,
        "intent": recipe.intent,
        "description": recipe.description,
        "summaryMd": recipe.summary_md,
        "request": recipe.request,
        "corrections": recipe.corrections,
        "pitfalls": recipe.pitfalls,
        "governingRules": recipe.governing_rules,
        "steps": recipe.steps,
        "touchedFiles": recipe.touched_files,
        "contributingSlices": recipe.contributing_slices,
        "rationale": recipe.rationale,
        "useWhen": recipe.use_when,
        "inputs": recipe.inputs,
        "outputs": recipe.outputs,
        "recovery": recipe.recovery,
        "language": recipe.language,
        "rev": recipe.rev,
        "parentRecipeId": recipe.parent_recipe_id,
        "sourceJobId": recipe.source_job_id,
        "userEdited": recipe.user_edited,
        "lastEditedBy": recipe.last_edited_by,
        "error": recipe.error,
        "createdAt": iso(recipe.created_at),
        "updatedAt": iso(recipe.updated_at),
        "resolvedAt": opt_iso(recipe.resolved_at),
    }


def recipe_application_view(application: RecipeApplication) -> JsonObject:
    """적용 이력 한 건의 칸이며 사건 좌표는 화면이 쓰지 않으므로 싣지 않는다."""
    return {
        "id": application.id,
        "userId": application.user_id,
        "recipeId": application.recipe_id,
        "taskId": application.task_id,
        "injectedVia": application.injected_via,
        "outcome": application.outcome,
        "note": application.note,
        "createdAt": iso(application.created_at),
    }


def cited_task_ids(recipes: list[Recipe]) -> list[str]:
    """모양을 신뢰할 수 없는 슬라이스에서 인용된 태스크 식별자만 중복 없이 모은다."""
    found: list[str] = []
    for recipe in recipes:
        for one in recipe.contributing_slices:
            if not isinstance(one, dict):
                continue
            task_id = one.get("taskId")
            if isinstance(task_id, str) and task_id and task_id not in found:
                found.append(task_id)
    return found
