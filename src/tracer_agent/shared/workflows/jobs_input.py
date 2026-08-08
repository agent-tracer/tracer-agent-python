"""잡 종류마다 접수가 받는 도메인 입력과 워크플로에 넘길 액티비티 입력을 소유한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agents.recipe_scan.models import scan_anchor_conditions, scan_anchor_requirements
from ..agents.shared.job_kinds import AgentJobKind
from ..agents.shared.json_view import JsonObject
from ..agents.shared.models import TrimmedStr
from .jobs_anchor import ScanAnchorSource

MAX_SUGGESTIONS_CAP = 50
MAX_RULES_CAP = 20
INTENT_MAX_LENGTH = 500


@dataclass(frozen=True)
class AdmissionRejection:
    """접수가 잡을 받아들이지 않을 때 계약이 정한 상태와 코드와 문구다."""

    status: int
    code: str
    message: str


INELIGIBLE_SCAN_ANCHOR = AdmissionRejection(
    400, "job.invalid-scan-anchor", "Recipe scan requires a completed root user task"
)


@dataclass(frozen=True)
class AdmissionContext:
    """접수가 잡 하나를 받아들일지 판정할 때 보는 사용자와 앵커 창구다."""

    user_id: str
    scan_anchors: ScanAnchorSource


class JobInput(BaseModel):
    """잡 종류마다의 접수 입력이며 자기 자격 심사를 스스로 갖는다."""

    model_config = ConfigDict(extra="forbid")

    async def admit(self, _context: AdmissionContext) -> AdmissionRejection | None:
        """받아들일 수 없는 사유를 내며 앵커를 요구하지 않는 잡은 아무 사유도 내지 않는다."""
        return None

    def task_id(self) -> str | None:
        """원장의 task_id 칸에 실을 값이며 태스크에 매이지 않은 잡은 아무것도 내지 않는다."""
        return None

    def activity_payload(self, base: JsonObject) -> JsonObject:
        """접수가 세운 공통 칸 위에 이 종류만 싣는 칸을 얹어 액티비티 입력을 짓는다."""
        return dict(base)


class RecipeScanJobInput(JobInput):
    """recipe-scan 접수가 받는 도메인 입력이며 태스크 소유권은 워커의 도구가 다시 검증한다."""

    taskId: TrimmedStr = Field(min_length=1, max_length=64)
    userPrompt: TrimmedStr | None = Field(default=None, min_length=1, max_length=4000)
    language: TrimmedStr | None = Field(default=None, min_length=1, max_length=16)
    trigger: Literal["dashboard", "session"] | None = None

    async def admit(self, context: AdmissionContext) -> AdmissionRejection | None:
        """스캔의 앵커는 이 사용자의 뿌리 사용자 태스크이면서 끝난 것이어야 한다."""
        anchor = await context.scan_anchors.find(context.user_id, self.taskId)
        if anchor is None:
            return INELIGIBLE_SCAN_ANCHOR
        eligible = anchor.eligible(scan_anchor_requirements(), scan_anchor_conditions(self.trigger))
        return None if eligible else INELIGIBLE_SCAN_ANCHOR

    def task_id(self) -> str | None:
        return self.taskId

    def activity_payload(self, base: JsonObject) -> JsonObject:
        language = {} if self.language is None else {"language": self.language}
        return {**base, "taskId": self.taskId, **language, "userPrompt": self.userPrompt}


class TitleSuggestionJobInput(JobInput):
    """title-suggestion 접수가 받는 도메인 입력이며 대화 컨텍스트는 워커가 직접 조립한다."""

    taskId: TrimmedStr = Field(min_length=1, max_length=64)

    def task_id(self) -> str | None:
        return self.taskId

    def activity_payload(self, base: JsonObject) -> JsonObject:
        return {**base, "taskId": self.taskId}


class TaskCleanupFilters(BaseModel):
    """task-cleanup 접수가 받는 선택적 조정치다."""

    model_config = ConfigDict(extra="forbid")

    maxSuggestions: int | None = Field(default=None, ge=1, le=MAX_SUGGESTIONS_CAP)


class TaskCleanupJobInput(JobInput):
    """task-cleanup 접수가 받는 도메인 입력이며 후보 배치는 워커가 직접 조립한다."""

    filters: TaskCleanupFilters = Field(default_factory=TaskCleanupFilters)

    def activity_payload(self, base: JsonObject) -> JsonObject:
        requested = self.filters.maxSuggestions
        return {**base, **({} if requested is None else {"maxSuggestions": requested})}


# 잡 종류마다 다른 접수 입력 모델이며 워커가 스스로 채우는 문맥·후보 배치는 여기 싣지 않는다.
INPUT_MODEL_BY_KIND: dict[str, type[JobInput]] = {
    AgentJobKind.RECIPE_SCAN.wire: RecipeScanJobInput,
    AgentJobKind.TITLE_SUGGESTION.wire: TitleSuggestionJobInput,
    AgentJobKind.TASK_CLEANUP.wire: TaskCleanupJobInput,
}


def build_payload(
    job_input: JobInput,
    user_id: str,
    execution_id: str,
    idempotency_key: str | None,
) -> JsonObject:
    """잡 종류에 맞는 액티비티 입력을 지으며 문맥과 후보 배치와 설정에서 오는 칸은 준비가 채운다."""
    return job_input.activity_payload(
        {"userId": user_id, "executionId": execution_id, "idempotencyKey": idempotency_key}
    )


# 같은 멱등키의 두 접수가 같은 입력인지 구분하는 칸이며 종류마다 이 순서로 적는다.
IDEMPOTENCY_KEYS: dict[str, tuple[str, ...]] = {
    AgentJobKind.TITLE_SUGGESTION.wire: ("taskId",),
    AgentJobKind.RECIPE_SCAN.wire: ("taskId", "userPrompt", "language", "trigger"),
    AgentJobKind.TASK_CLEANUP.wire: ("filters.maxSuggestions",),
}


def canonical_input(kind: str, job_input: BaseModel) -> str:
    """두 구현체가 같은 바이트를 만들도록 그 종류가 정한 칸만 정해진 순서로 적는다."""
    dumped = job_input.model_dump(mode="json")
    canonical = {key: _read_path(dumped, key) for key in IDEMPOTENCY_KEYS[kind]}
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))


def input_hash(kind: str, job_input: BaseModel) -> str:
    """접수가 다듬은 도메인 입력의 안정 해시이며 멱등 판정은 이 값만 본다."""
    return hashlib.sha256(canonical_input(kind, job_input).encode()).hexdigest()


def _read_path(dumped: Any, key: str) -> Any:
    value: Any = dumped
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
