"""잡 종류마다 접수가 받는 도메인 입력과 워크플로에 넘길 액티비티 입력을 소유한다."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_SUGGESTIONS = 20
MAX_SUGGESTIONS_CAP = 50
MAX_RULES_CAP = 20
INTENT_MAX_LENGTH = 500


class RecipeScanJobInput(BaseModel):
    """recipe-scan 접수가 받는 도메인 입력이며 태스크 소유권은 워커의 도구가 다시 검증한다."""

    model_config = ConfigDict(extra="forbid")

    taskId: str = Field(min_length=1, max_length=64)
    userPrompt: str | None = Field(default=None, min_length=1, max_length=4000)
    language: str | None = Field(default=None, min_length=1, max_length=16)
    trigger: Literal["dashboard", "session"] | None = None


class TitleSuggestionJobInput(BaseModel):
    """title-suggestion 접수가 받는 도메인 입력이며 대화 컨텍스트는 워커가 직접 조립한다."""

    model_config = ConfigDict(extra="forbid")

    taskId: str = Field(min_length=1, max_length=64)


class TaskCleanupFilters(BaseModel):
    """task-cleanup 접수가 받는 선택적 조정치다."""

    model_config = ConfigDict(extra="forbid")

    maxSuggestions: int | None = Field(default=None, ge=1, le=MAX_SUGGESTIONS_CAP)


class TaskCleanupJobInput(BaseModel):
    """task-cleanup 접수가 받는 도메인 입력이며 후보 배치는 워커가 직접 조립한다."""

    model_config = ConfigDict(extra="forbid")

    filters: TaskCleanupFilters = Field(default_factory=TaskCleanupFilters)


class RuleGenerationJobInput(BaseModel):
    """rule-generation 접수가 받는 도메인 입력이며 로컬 실행기가 이 잡을 가져간다."""

    model_config = ConfigDict(extra="forbid")

    taskId: str = Field(min_length=1, max_length=64)
    # 규칙이 매달릴 근거 입력이며 판정은 이 입력 이후의 이벤트만 본다.
    anchorEventId: str = Field(min_length=1, max_length=64)
    focus: Literal["recent"] | None = None
    maxRules: int | None = Field(default=None, ge=1, le=MAX_RULES_CAP)
    intent: str | None = Field(default=None, min_length=1, max_length=INTENT_MAX_LENGTH)


# 잡 종류마다 다른 접수 입력 모델이며 워커가 스스로 채우는 문맥·후보 배치는 여기 싣지 않는다.
INPUT_MODEL_BY_KIND: dict[str, type[BaseModel]] = {
    "recipe.scan": RecipeScanJobInput,
    "title.suggestion": TitleSuggestionJobInput,
    "task.cleanup": TaskCleanupJobInput,
    "rule.generation": RuleGenerationJobInput,
}


def task_id_of(job_input: BaseModel) -> str | None:
    """태스크에 매인 잡 종류만 원장의 task_id 칸에 실을 값을 갖는다."""
    task_id = getattr(job_input, "taskId", None)
    return task_id if isinstance(task_id, str) else None


def build_payload(
    job_input: BaseModel,
    user_id: str,
    execution_id: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """잡 종류에 맞는 액티비티 입력을 지으며 문맥과 후보 배치와 한도는 워커 액티비티가 스스로 채운다."""
    base = {
        "userId": user_id,
        "executionId": execution_id,
        "idempotencyKey": idempotency_key,
    }
    if isinstance(job_input, RecipeScanJobInput):
        return {
            **base,
            "taskId": job_input.taskId,
            "language": job_input.language or "auto",
            "userPrompt": job_input.userPrompt,
        }
    if isinstance(job_input, TitleSuggestionJobInput):
        return {**base, "taskId": job_input.taskId, "language": "auto"}
    assert isinstance(job_input, TaskCleanupJobInput)
    return {
        **base,
        "language": "auto",
        "maxSuggestions": job_input.filters.maxSuggestions or DEFAULT_MAX_SUGGESTIONS,
    }
