"""레시피 창구가 받는 질의와 본문의 스키마를 소유한다."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

RecipeStatusValue = Literal["candidate", "active", "dismissed", "superseded", "retired"]
RecipeOutcomeValue = Literal["completed", "abandoned", "superseded"]

_Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
_Intent = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
_SummaryMd = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
_TaskId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

# 실은 칸에 비움을 적으면 선택 칸을 뺀 것과 같아지므로 두 요청을 갈라 받는다.
_NULL_IS_NOT_ABSENT = "field carries null instead of being absent"


def _reject_explicit_null(body: BaseModel) -> None:
    if any(getattr(body, name) is None for name in body.model_fields_set):
        raise ValueError(_NULL_IS_NOT_ABSENT)


class ListRecipesQuery(BaseModel):
    """목록 창구가 받는 질의이며 상태를 싣지 않으면 모든 상태를 낸다."""

    model_config = ConfigDict(extra="ignore")

    status: RecipeStatusValue | None = None


class SearchRecipesQuery(BaseModel):
    """검색 창구가 받는 질의이며 상한은 계약이 정한 범위 안에서만 받는다."""

    model_config = ConfigDict(extra="ignore")

    q: str = Field(min_length=1)
    limit: int | None = Field(default=None, ge=1, le=10)


class RecipeEditBody(BaseModel):
    """채택된 레시피에서 사람이 고칠 수 있는 칸이며 하나 이상을 실어야 한다."""

    model_config = ConfigDict(extra="forbid")

    title: _Title | None = None
    intent: _Intent | None = None
    description: _Description | None = None
    summaryMd: _SummaryMd | None = None

    @model_validator(mode="after")
    def require_one_field(self) -> RecipeEditBody:
        """고칠 칸을 하나도 싣지 않은 요청은 받지 않는다."""
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        _reject_explicit_null(self)
        return self

    def revision(self) -> dict[str, str]:
        """실은 칸만 원장의 이름으로 옮긴다."""
        named = {
            "title": self.title,
            "intent": self.intent,
            "description": self.description,
            "summary_md": self.summaryMd,
        }
        return {name: value for name, value in named.items() if value is not None}


class RecipeOutcomeBody(BaseModel):
    """자기보고가 싣는 태스크와 결과와 덧붙이는 한 문장이다."""

    model_config = ConfigDict(extra="forbid")

    taskId: _TaskId
    outcome: RecipeOutcomeValue
    note: _Note | None = None

    @model_validator(mode="after")
    def reject_null_note(self) -> RecipeOutcomeBody:
        """덧붙임을 비움으로 실은 요청은 싣지 않은 것과 갈라 받는다."""
        _reject_explicit_null(self)
        return self
