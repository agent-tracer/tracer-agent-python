"""chat 실행 수명의 큐와 식별자와 단계 사이 payload를 워크플로가 그대로 쓸 수 있게 소유한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import task_queue

CHAT_QUEUE_KEY = "chat"
CHAT_TASK_QUEUE = task_queue(CHAT_QUEUE_KEY)

CHAT_THREAD_WORKFLOW = "chatThreadWorkflow"
CHAT_EXECUTION_WORKFLOW = "chatExecutionWorkflow"
THREAD_WORKFLOW_PREFIX = "chat-thread"
EXECUTION_WORKFLOW_PREFIX = "chat"

CHAT_ENQUEUE_SIGNAL = "enqueueChatExecution"

# 실행 갱신을 다른 replica의 SSE에 알리는 토픽이며 식별자만 나른다.
CHAT_EXECUTION_UPDATES_TOPIC = "chat.execution.updates"

PREPARE_ACTIVITY = "prepareChatExecution"
GENERATE_ACTIVITY = "generateChatExecution"
FINALIZE_ACTIVITY = "finalizeChatExecution"
FAIL_ACTIVITY = "failChatExecution"

# 스레드가 다른 실행에 잠겨 준비를 못 한 것이며, 이 실행의 실패가 아니다.
THREAD_BUSY_FAILURE = "chat.thread-busy"
THREAD_BUSY_RETRY_S = 60.0
# 회수 유예를 넘겨 기다려야 잠긴 스레드가 풀리는 것을 보고 다시 집을 수 있다.
THREAD_BUSY_MAX_ROUNDS = 45

# 한 시도의 벽시계 상한을 넘겨 갱신이 끊긴 running만 주인이 사라진 것으로 본다.
RUNNING_LEASE_S = 20 * 60.0

GENERATE_MAX_ATTEMPTS = 3
STAGE_MAX_ATTEMPTS = 5
PREPARE_TIMEOUT_S = 120.0
GENERATE_TIMEOUT_S = 15 * 60.0
GENERATE_HEARTBEAT_TIMEOUT_S = 30.0
HEARTBEAT_INTERVAL_S = 10.0
FINALIZE_TIMEOUT_S = 120.0
FAIL_TIMEOUT_S = 60.0

# 신호가 끊긴 스레드는 이만큼 조용하면 다음 턴을 새 워크플로가 연다.
THREAD_IDLE_S = 5.0
# 이력이 무한정 자라지 않도록 이 만큼 돌린 스레드는 대기 줄을 안고 새 실행으로 넘어간다.
THREAD_MAX_CHILDREN = 100

STOP_COMPLETED = "completed"
STOP_BUDGET_LANDED = "budget_landed"
STOP_CANCELED = "canceled"


def thread_workflow_id(thread_id: str) -> str:
    """스레드 하나에 하나뿐인 워크플로 식별자를 만든다."""
    return f"{THREAD_WORKFLOW_PREFIX}:{thread_id}"


def execution_workflow_id(execution_id: str) -> str:
    """실행 하나에 하나뿐인 워크플로 식별자를 만든다."""
    return f"{EXECUTION_WORKFLOW_PREFIX}:{execution_id}"


@dataclass
class ChatExecutionRequest:
    """실행 하나를 워크플로에 넘기는 입력이며 식별자만 나른다."""

    execution_id: str
    thread_id: str


@dataclass
class ChatThreadRequest:
    """스레드 하나의 턴을 직렬로 흘리는 워크플로 입력이며 아직 못 돌린 실행을 함께 나른다."""

    thread_id: str
    pending: list[ChatExecutionRequest] = field(default_factory=list)


@dataclass
class PreparedChatExecution:
    """원장이 running 자리를 내준 실행의 사실이며 생성 단계가 이것으로 봉투를 덮어쓴다."""

    execution_id: str
    thread_id: str
    user_id: str
    language: str
    model: str | None = None


@dataclass
class GeneratedChatExecution:
    """한 턴이 만든 산출물과 지출 전부이며 종결 단계가 원장에 그대로 적는다."""

    execution_id: str
    thread_id: str
    user_id: str
    attempt: int
    canceled: bool
    text: str
    model_used: str
    stop_reason: str
    cost_usd: float | None = None
    num_turns: int | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    observation: dict[str, Any] = field(default_factory=dict)


@dataclass
class FailedChatExecution:
    """실행 하나를 사유와 함께 실패로 닫아 달라는 요청이다."""

    execution_id: str
    error: str
