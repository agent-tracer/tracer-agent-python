"""title-suggestion의 실행 봉투와 내부 구조화 체인 계약."""

from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

from ..shared.graph_state import SpendChannels, TurnCeilingState, fresh_spend_channels
from ..shared.models import (
    AgentExecutionRequest,
    Language,
    ModelFacing,
    TrimmedStr,
)

RECENT_TURN_LIMIT = 20
# 워커는 최근 창에 최초 턴 하나를 더 올려 보내므로 컨텍스트가 실을 수 있는 턴은 하나 더 많다.
MAX_CONTEXT_TURNS = RECENT_TURN_LIMIT + 1


class TitleSuggestionTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 컨텍스트가 첫 턴과 최근 창만 싣고 가운데를 잘라내므로 번호로 빠진 구간을 드러낸다.
    turnIndex: int = Field(ge=0)
    askedText: str
    assistantText: str | None = None


class TitleSuggestionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    status: str
    workspacePath: str | None = None
    totalEventCount: int = Field(ge=0)
    totalTurnCount: int = Field(ge=0)
    truncated: bool
    turns: list[TitleSuggestionTurn] = Field(max_length=MAX_CONTEXT_TURNS)


class TitleSuggestionRequest(AgentExecutionRequest):
    """Python-native title-suggestion의 도메인 실행 봉투."""

    model_config = ConfigDict(extra="forbid")

    taskId: TrimmedStr = Field(min_length=1)
    language: Language = "auto"
    context: TitleSuggestionContext


class TitleSuggestion(ModelFacing):
    title: TrimmedStr = Field(min_length=1, max_length=80)
    rationale: TrimmedStr = Field(min_length=1, max_length=200)


class TitleSuggestionDraft(ModelFacing):
    suggestions: list[TitleSuggestion] = Field(default_factory=list, max_length=3)


class InvestigateUpdate(SpendChannels):
    """결정 노드가 갱신하는 상태 부분집합이다."""

    candidate: TitleSuggestionDraft
    messages: list[BaseMessage]


class ValidateCandidateUpdate(TypedDict):
    """검증 노드가 갱신하는 상태 부분집합이다."""

    # 쓸모없는 후보를 떨어뜨린 결과를 함께 써서 종단이 걸러진 목록을 낸다.
    candidate: TitleSuggestionDraft | None
    validation_errors: list[str]


class RepairUpdate(SpendChannels):
    """수리 노드가 갱신하는 상태 부분집합이다."""

    candidate: TitleSuggestionDraft
    messages: list[BaseMessage]
    repair_attempted: bool


class ResultUpdate(TypedDict):
    """종단 노드가 갱신하는 상태 부분집합이다."""

    result: TitleSuggestionDraft


class TitleSuggestionState(TurnCeilingState):
    task_id: str
    language: Language
    context: TitleSuggestionContext
    messages: list[BaseMessage]
    candidate: TitleSuggestionDraft | None
    validation_errors: list[str]
    repair_attempted: bool
    result: TitleSuggestionDraft | None


def initial_title_suggestion_state(
    req: TitleSuggestionRequest, *, max_cost_usd: float, max_turns: int
) -> TitleSuggestionState:
    """실행을 처음 시작하는 상태이며 예약을 뗀 뒤 조사가 쓸 몫을 함께 싣는다."""
    return {
        "task_id": req.taskId,
        "max_cost_usd": max_cost_usd,
        "max_turns": max_turns,
        "language": req.language,
        "context": req.context,
        "messages": [],
        "candidate": None,
        "validation_errors": [],
        "repair_attempted": False,
        "result": None,
        **fresh_spend_channels(),
    }
