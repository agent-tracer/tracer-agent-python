"""title·recipe·cleanup 잡의 큐와 워크플로 이름과 액티비티 입력 payload를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass

from ..agents.shared.json_view import JsonObject
from ..config import task_queue
from .jobs_kinds import AgentJobKind

JOBS_QUEUE_KEY = "jobs"
GENERATE_QUEUE_KEY = "generate"

JOBS_TASK_QUEUE = task_queue(JOBS_QUEUE_KEY)
# 최대 15분인 모델 호출이 짧은 액티비티의 슬롯을 점유하지 않도록 생성만 이 큐에서 실행된다.
GENERATE_TASK_QUEUE = task_queue(GENERATE_QUEUE_KEY)

# 사용자의 열린 화면에 상태 전이를 알리는 토픽이며 봉투와 종류는 계약이 소유한다.
NOTIFICATIONS_TOPIC = "notifications"
JOB_UPDATED_NOTIFICATION = "job.updated"

AGENT_JOB_WORKFLOW = "agentJobWorkflow"
PREPARE_AGENT_JOB_ACTIVITY = "prepareAgentJob"
GENERATE_AGENT_JOB_ACTIVITY = "generateAgentJob"
FINALIZE_AGENT_JOB_ACTIVITY = "finalizeAgentJob"
FAIL_AGENT_JOB_ACTIVITY = "failAgentJob"
# activity가 돌기 전에 취소가 닿으면 워크플로가 이 액티비티로 원장을 직접 닫는다.
SETTLE_CANCELED_JOB_ACTIVITY = "settleCanceledAgentJob"

# 추적 창구 왕복과 원장 전이뿐이라 모델을 부르는 단계보다 짧게 잡는다.
JOB_PREPARE_TIMEOUT_S = 60.0
JOB_PREPARE_MAX_ATTEMPTS = 5
# 생성 하나의 벽시계 상한이며 가장 긴 recipe-scan 데드라인에 배달 여유를 더했다.
JOB_GENERATE_TIMEOUT_S = 900.0
JOB_GENERATE_SCHEDULE_TO_CLOSE_S = 1200.0
JOB_GENERATE_MAX_ATTEMPTS = 3
# 원장 종결과 산출물 배달뿐이라 생성보다 짧게 잡는다.
JOB_FINALIZE_TIMEOUT_S = 60.0
JOB_FINALIZE_MAX_ATTEMPTS = 5
# 이 안에 하트비트가 없으면 Temporal이 유실로 보고 재시도를 시작하므로 취소 지연보다 넉넉히 잡는다.
JOB_HEARTBEAT_TIMEOUT_S = 30.0
JOB_HEARTBEAT_INTERVAL_S = 10.0
# 원장 갱신 한 문장뿐이라 실행 액티비티보다 훨씬 짧게 잡는다.
JOB_CANCEL_SETTLE_TIMEOUT_S = 30.0


def agent_job_workflow_id(kind: AgentJobKind, key: str) -> str:
    """잡 워크플로 식별자를 잡 종류와 접수 키로 만든다."""
    return f"job:{kind}:{key}"


@dataclass
class AgentJobRequest:
    """접수가 워크플로에 넘기는 입력이며 도메인 값만 싣는다."""

    kind: AgentJobKind
    payload: JsonObject


@dataclass
class AgentJobSettlement:
    """생성이 끝낸 실행을 종결이 원장에 적는 데 필요한 값이며 자격을 싣지 않는다."""

    kind: AgentJobKind
    payload: JsonObject
    outcome: JsonObject
    response: JsonObject
