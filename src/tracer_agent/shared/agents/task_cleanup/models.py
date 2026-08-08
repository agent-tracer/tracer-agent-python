"""task-cleanup의 HTTP 봉투, 그래프 상태, 구조화 체인 계약."""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..shared.dispatch_depth import DispatchDepth, depth_share
from ..shared.graph_state import SpendChannels, TurnCeilingState, fresh_spend_channels
from ..shared.models import AgentExecutionRequest, Language, TrimmedStr

# 저장 계약의 판별자와 같은 값이어야 하는 정리 제안의 종류다.
CleanupSuggestionKind = Literal["archive"]

MAX_SUGGESTIONS = 50
MAX_EVIDENCE_EVENT_IDS = 100

# 워커의 도구 응답을 그대로 받으므로 워커가 필드를 늘려도 실행이 깨지지 않게 모르는 필드는 버린다.
_TOOL_RESPONSE = ConfigDict(extra="ignore")


class CandidateReason(StrEnum):
    """서버가 결정론적으로 찾아낸 정리 후보의 신호다."""

    NO_EVENTS = "no-events"
    DUPLICATE_TITLE = "duplicate-title"
    PLACEHOLDER_TITLE = "placeholder-title"
    STALE = "stale"


class CleanupTaskStatus(StrEnum):
    """정리 판정이 아직 진행 중인 태스크로 보는 상태다."""

    RUNNING = "running"
    WAITING = "waiting"


class CleanupCandidate(BaseModel):
    model_config = _TOOL_RESPONSE

    id: TrimmedStr = Field(min_length=1)
    # 워커는 태스크 제목을 가공하지 않고 내주므로 제목이 빈 문자열일 수 있다.
    visibleTitle: str
    status: TrimmedStr = Field(min_length=1)
    lastEventAt: str | None
    hasEvents: bool
    activeChildCount: int = Field(ge=0)
    candidateReasons: list[CandidateReason]


class CleanupBatch(BaseModel):
    """서버가 이번 스캔 대상으로 미리 자격 심사한 후보 배치다."""

    model_config = _TOOL_RESPONSE

    candidates: list[CleanupCandidate] = Field(default_factory=list)
    # 서버 조회 상한에 걸려 이 배치가 후보 전체를 담지 못했는지 여부다.
    batchTruncated: bool = False
    # 후보를 가리기 전에 조회한 태스크 수이며 제안에서 되짚을 수 없어 산출이 따로 싣는다.
    tasksScanned: int = 0


class TaskCleanupRequest(AgentExecutionRequest):
    """Python-native task-cleanup이 받는 도메인 실행 봉투."""

    model_config = ConfigDict(extra="forbid")

    scannedAt: TrimmedStr = Field(min_length=1)
    language: Language = "auto"
    maxSuggestions: int = Field(ge=1, le=MAX_SUGGESTIONS)
    batch: CleanupBatch


class CleanupEvent(BaseModel):
    model_config = _TOOL_RESPONSE

    id: TrimmedStr = Field(min_length=1)
    seq: TrimmedStr = Field(min_length=1)
    kind: TrimmedStr = Field(min_length=1)
    title: str
    body: str | None = None
    toolName: str | None = None
    filePaths: list[str] = Field(default_factory=list)
    occurredAt: TrimmedStr = Field(min_length=1)


class EventPage(BaseModel):
    model_config = _TOOL_RESPONSE

    events: list[CleanupEvent]
    truncated: bool
    nextCursor: str | None = None
    total: int = Field(ge=0)


# 검토 전문가 인스턴스의 턴 백스톱이며 SDK 대응 상수와 계약으로 값을 맞춘다.
MAX_INSPECT_TURNS = 4
# 조율자가 고른 깊이를 예산 배분의 몫으로 옮기는 계약의 자리다.
INSPECT_DEPTH_KEY = "inspectDepth"
MAX_INSPECT_EXCERPTS = 6
MAX_INSPECT_REASON_CHARS = 400
CLEANUP_REVIEWER_ROLE = "cleanup-candidate-reviewer"

# 조율자가 결정 대신 후보를 다시 조회하게 할 수 있는 라운드 수이며 무한 루프를 이 값으로 막는다.
MAX_REDISPATCH_ROUNDS = 1


class InspectAssignment(BaseModel):
    """조율자가 조회하기로 고른 후보 하나와 고른 조사 깊이다."""

    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1)
    depth: DispatchDepth

    @property
    def share(self) -> int:
        """이 검토자가 예산 배분에서 받는 몫이며 값은 계약이 갖는다."""
        return depth_share("task-cleanup", INSPECT_DEPTH_KEY, self.depth)


class TriagePlan(BaseModel):
    """조율자가 후보 배치를 보고 무엇을 조사할지 정한 결과다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    assignments: list[InspectAssignment] = Field(
        default_factory=list, alias="inspect", max_length=MAX_SUGGESTIONS
    )

    def total_share(self) -> int:
        """계획이 요구하는 몫의 합이다."""
        return sum(item.share for item in self.assignments)


class InspectDispatch(BaseModel):
    """조율자가 후보 조사 분기 하나에 실어 보내는 조사 지시와 배분한 턴과 달러 몫이다."""

    model_config = ConfigDict(extra="forbid")

    assignment: InspectAssignment
    # 0턴은 그 검토자에게 몫이 남지 않았다는 뜻이며 노드가 모델을 부르지 않는 길로 스스로 보낸다.
    max_turns: int = Field(ge=0)
    cost_budget: float = Field(ge=0.0)


class InspectReport(BaseModel):
    """한 후보를 조회한 결과이며 조율자는 이것만 보고 제안을 쓴다."""

    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1)
    archivable: bool
    reason: TrimmedStr = Field(min_length=1, max_length=MAX_INSPECT_REASON_CHARS)
    citedEventIds: list[TrimmedStr] = Field(default_factory=list, max_length=MAX_INSPECT_EXCERPTS)


def merged_candidates(
    left: dict[str, CleanupCandidate], right: dict[str, CleanupCandidate]
) -> dict[str, CleanupCandidate]:
    """병렬 조사가 노출한 후보를 하나의 목록으로 합친다."""
    return {**left, **right}


def merged_event_ids(left: dict[str, set[str]], right: dict[str, set[str]]) -> dict[str, set[str]]:
    """태스크마다 실제로 읽은 이벤트를 합집합으로 합친다."""
    combined = {task_id: set(ids) for task_id, ids in left.items()}
    for task_id, ids in right.items():
        combined.setdefault(task_id, set()).update(ids)
    return combined


class CleanupDraftSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CleanupSuggestionKind = Field(description='The suggestion kind, always "archive"')
    taskId: TrimmedStr = Field(min_length=1)
    rationale: TrimmedStr = Field(min_length=1, max_length=500)
    evidenceEventIds: list[TrimmedStr] = Field(
        default_factory=list,
        max_length=MAX_EVIDENCE_EVENT_IDS,
        description="Event IDs get_task_events returned for this task and that back the rationale",
    )


class CleanupDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[CleanupDraftSuggestion] = Field(default_factory=list, max_length=MAX_SUGGESTIONS)
    # 조율자는 결정 대신 후보를 한 번 더 조회하게 요청할 수 있으며 둘은 함께 오지 않는다.
    redispatch: list[InspectAssignment] = Field(default_factory=list, max_length=MAX_SUGGESTIONS)

    @model_validator(mode="after")
    def _draft_or_redispatch(self) -> CleanupDraft:
        if self.suggestions and self.redispatch:
            raise ValueError("return either suggestions or a redispatch request, not both")
        return self


class TriageUpdate(SpendChannels):
    """조율자 노드가 갱신하는 상태 부분집합이다."""

    plan: TriagePlan
    exposed_candidates: dict[str, CleanupCandidate]
    event_ids_by_task: dict[str, set[str]]


class InspectUpdate(SpendChannels):
    """후보 조사 노드가 갱신하는 상태 부분집합이다."""

    reports: list[InspectReport]
    event_ids_by_task: dict[str, set[str]]


class InvestigateUpdate(SpendChannels):
    """결정 노드가 갱신하는 상태 부분집합이다."""

    suggestions: list[CleanupDraftSuggestion]
    messages: list[BaseMessage]
    exposed_candidates: dict[str, CleanupCandidate]
    event_ids_by_task: dict[str, set[str]]
    # None이면 검증으로 넘어가고, 계획이 담기면 그 후보들을 한 번 더 조회한다.
    redispatch: TriagePlan | None
    redispatch_count: int


class ValidateDecisionsUpdate(TypedDict):
    """검증 노드가 갱신하는 상태 부분집합이다."""

    suggestions: list[CleanupDraftSuggestion]
    validation_errors: list[str]


class RepairUpdate(SpendChannels):
    """수리 노드가 갱신하는 상태 부분집합이다."""

    suggestions: list[CleanupDraftSuggestion]
    messages: list[BaseMessage]
    exposed_candidates: dict[str, CleanupCandidate]
    event_ids_by_task: dict[str, set[str]]
    repair_attempted: bool


class CleanupResult(BaseModel):
    """정리 스캔이 창구에 내는 산출물이다."""

    suggestions: list[CleanupDraftSuggestion] = Field(default_factory=list)
    tasksScanned: int = 0


class ResultUpdate(TypedDict):
    """종단 노드가 갱신하는 상태 부분집합이다."""

    result: CleanupResult


class TaskCleanupState(TurnCeilingState):
    scanned_at: str
    tasks_scanned: int
    language: Language
    max_suggestions: int
    messages: list[BaseMessage]
    plan: TriagePlan | None
    # 조율자가 결정 대신 요청한 추가 조사 계획이며 없으면 검증으로 넘어간다.
    redispatch: TriagePlan | None
    # 추가 파견의 상한을 지키기 위해 센 파견 횟수다.
    redispatch_count: int
    # 후보마다 병렬로 조회하므로 노출·인용·지출이 모두 누적으로 합쳐져야 한다.
    reports: Annotated[list[InspectReport], operator.add]
    exposed_candidates: Annotated[dict[str, CleanupCandidate], merged_candidates]
    event_ids_by_task: Annotated[dict[str, set[str]], merged_event_ids]
    suggestions: list[CleanupDraftSuggestion]
    validation_errors: list[str]
    repair_attempted: bool
    result: CleanupResult | None


def initial_task_cleanup_state(
    req: TaskCleanupRequest, *, max_cost_usd: float, max_turns: int
) -> TaskCleanupState:
    """실행을 처음 시작하는 상태이며 예약을 뗀 뒤 팬아웃이 나눌 몫을 함께 싣는다."""
    return {
        "scanned_at": req.scannedAt,
        "tasks_scanned": req.batch.tasksScanned,
        "language": req.language,
        "max_suggestions": req.maxSuggestions,
        "messages": [],
        "plan": None,
        "redispatch": None,
        "redispatch_count": 0,
        "reports": [],
        "exposed_candidates": {},
        "event_ids_by_task": {},
        "max_cost_usd": max_cost_usd,
        "max_turns": max_turns,
        "suggestions": [],
        "validation_errors": [],
        "repair_attempted": False,
        "result": None,
        **fresh_spend_channels(),
    }
