"""chat 대화 에이전트의 실행 봉투와 그래프 상태와 결과 계약."""

from __future__ import annotations

from typing import Literal, TypedDict, get_args

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

from ..shared.json_view import JsonObject
from ..shared.models import AgentExecutionEnvelope, Language, TrimmedStr

ChatMessageRole = Literal["user", "assistant", "tool"]
CHAT_MESSAGE_ROLES: tuple[str, ...] = get_args(ChatMessageRole)

ChatExecutionStatus = Literal["queued", "running", "completed", "failed", "canceled"]
CHAT_EXECUTION_STATUSES: tuple[str, ...] = get_args(ChatExecutionStatus)

# 더 전진하지 않는 실행의 상태이며 이 자리에 닿으면 열린 연결도 통지도 끝난다.
TERMINAL_CHAT_EXECUTION_STATUSES: tuple[str, ...] = ("completed", "failed", "canceled")

# 모델이 왜 말을 멈췄는지이며 실행 수명을 나타내는 status와 다른 축이다.
ChatStopReason = Literal[
    "completed", "deadline", "stalled", "budget_landed", "turn_limit", "canceled", "failed"
]
CHAT_STOP_REASONS: tuple[str, ...] = get_args(ChatStopReason)

ChatConfirmationStatus = Literal["pending", "approved", "rejected"]
CHAT_CONFIRMATION_STATUSES: tuple[str, ...] = get_args(ChatConfirmationStatus)


class ChatHistoryToolCall(BaseModel):
    """저장된 어시스턴트 도구 호출을 LangChain 메시지로 되살리는 최소 계약이다."""

    model_config = ConfigDict(extra="ignore")

    id: TrimmedStr = Field(min_length=1)
    name: TrimmedStr = Field(min_length=1)
    args: JsonObject = Field(default_factory=dict)


class ChatHistoryMessage(BaseModel):
    """워커가 실어 보내는 대화 이력 한 줄이며 그래프가 모델 메시지로 되살린다."""

    model_config = ConfigDict(extra="ignore")

    role: ChatMessageRole
    content: str = ""
    toolCalls: list[ChatHistoryToolCall] = Field(default_factory=list)
    toolCallId: str | None = None


class ChatFact(BaseModel):
    """사용자에 대해 기억해 둔 지속 사실 하나다."""

    model_config = ConfigDict(extra="ignore")

    key: TrimmedStr = Field(min_length=1)
    content: str


class ChatReplay(BaseModel):
    """서버가 계산해 준 한 턴의 재생 문맥이며 창 자르기와 도구 호출 짝 맞추기가 이미 끝나 있다."""

    model_config = ConfigDict(extra="ignore")

    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    summary: str | None = None
    facts: list[ChatFact] = Field(default_factory=list)


class ChatTurnFields(BaseModel):
    """대화 턴 하나의 도메인 입력이며 실행 봉투가 이를 실어 전달한다."""

    threadId: TrimmedStr = Field(min_length=1)
    executionId: TrimmedStr = Field(min_length=1)
    # 조회 범위를 정하는 값이라 도메인 입력이며 멱등 해시에 함께 든다.
    userId: TrimmedStr = Field(min_length=1)
    language: Language = "auto"
    summary: str | None = None
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    facts: list[ChatFact] = Field(default_factory=list)
    # 읽기 도구가 추적 API를 사용자 범위로 되읽는 진입점이다.
    readApiBaseUrl: str = ""
    # 되읽기와 확인 창구와 장기기억이 매인 에이전트 자기 배포 단위의 진입점이다.
    agentApiBaseUrl: str = ""
    # 이 실행과 이 사용자에 매인 자격이며, 서버가 자기신고 헤더 대신 이것을 믿는다.
    scopeToken: str = ""
    # 모델에게 보일 도구 설명이며 두 백엔드가 같은 문장을 쓰도록 계약에서 워커가 실어 보낸다.
    toolDescriptions: dict[str, str] = Field(default_factory=dict)


class DraftCallback(BaseModel):
    """실행 도중 누적 답변을 되돌려 보내는 창구이며 실행 시도 하나에만 유효하다."""

    model_config = ConfigDict(extra="forbid")

    url: TrimmedStr = Field(min_length=1)
    token: TrimmedStr = Field(min_length=1)
    attempt: int = Field(ge=1)


class ChatRequest(ChatTurnFields, AgentExecutionEnvelope):
    """대화 턴 하나를 실행하는 내구성 실행 봉투."""

    model_config = ConfigDict(extra="forbid")

    executionId: TrimmedStr = Field(min_length=1)
    deadlineMs: int = 120_000
    draftCallback: DraftCallback | None = None


class ProposedWrite(BaseModel):
    """도구를 부른 시점에 확인 창구가 세워 준 대기 행 하나이며 워커가 그 id를 도구 호출로 인용한다."""

    model_config = ConfigDict(extra="forbid")

    confirmationId: TrimmedStr = Field(min_length=1)
    toolName: TrimmedStr = Field(min_length=1)
    args: JsonObject = Field(default_factory=dict)


class ChatResult(BaseModel):
    """대화 턴 하나의 구조화 결과이며 어시스턴트 답변과 확인 대기 행 인용을 담는다."""

    model_config = ConfigDict(extra="forbid")

    assistantText: str = ""
    proposedWrites: list[ProposedWrite] = Field(default_factory=list)


class LoadContextUpdate(TypedDict):
    """문맥 적재 노드가 갱신하는 상태 부분집합이다."""

    history: list[ChatHistoryMessage]
    summary: str | None
    facts: list[ChatFact]


class ConverseUpdate(TypedDict):
    """대화 노드가 갱신하는 상태 부분집합이다."""

    messages: list[BaseMessage]
    model_cost_usd: float
    proposals: list[ProposedWrite]


class SettleUpdate(TypedDict):
    """종결 노드가 갱신하는 상태 부분집합이다."""

    result: ChatResult


class ChatState(TypedDict):
    language: Language
    summary: str | None
    facts: list[ChatFact]
    # 문맥 적재가 되살린 이 턴의 재생 이력이며 대화가 이것으로 시작한다.
    history: list[ChatHistoryMessage]
    # 근거는 프롬프트에 다시 붙이지 않고 대화 이력에 남아 캐시된다.
    messages: list[BaseMessage]
    model_cost_usd: float
    # 대화가 세운 확인 대기 행이며 종결이 결과에 인용한다.
    proposals: list[ProposedWrite]
    result: ChatResult | None
