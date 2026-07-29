"""title·recipe·cleanup 잡의 큐와 워크플로 이름과 액티비티 입력 payload를 소유한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# TypeScript 구현체의 워커가 "sdk-jobs"를 폴링하므로 큐를 나눠야 두 구현체가 같은 실행을 가져가지 않는다.
GRAPH_JOB_QUEUE = "graph-jobs"

# 워크플로 이름과 액티비티 이름도 갈라 두 백엔드의 워크플로가 서로를 덮어쓰지 않게 한다.
AGENT_JOB_WORKFLOW = "graphAgentJobWorkflow"
RUN_AGENT_JOB_ACTIVITY = "runGraphAgentJob"
# activity가 돌기 전에 취소가 닿으면 워크플로가 이 액티비티로 원장을 직접 닫는다.
SETTLE_CANCELED_JOB_ACTIVITY = "settleCanceledGraphJob"

AgentJobKind = Literal["title-suggestion", "task-cleanup", "recipe-scan"]

# 액티비티 하나의 벽시계 상한이며 가장 긴 recipe-scan 데드라인에 접수와 배달 여유를 더했다.
JOB_TIMEOUT_S = 900.0
JOB_MAX_ATTEMPTS = 3
# 이 안에 하트비트가 없으면 Temporal이 유실로 보고 재시도를 태우므로 취소 지연보다 넉넉히 잡는다.
JOB_HEARTBEAT_TIMEOUT_S = 30.0
JOB_HEARTBEAT_INTERVAL_S = 10.0
# 원장 갱신 한 문장뿐이라 실행 액티비티보다 훨씬 짧게 잡는다.
JOB_CANCEL_SETTLE_TIMEOUT_S = 30.0


def agent_job_workflow_id(kind: AgentJobKind, key: str) -> str:
    """잡 워크플로 식별자를 잡 종류와 접수 키로 만들어 TypeScript 구현체의 것과 겹치지 않는다."""
    return f"graph-job:{kind}:{key}"


@dataclass
class AgentJobRequest:
    """접수가 워크플로에 넘기는 입력이며 파싱된 실행 봉투를 JSON으로 나른다."""

    kind: AgentJobKind
    payload: dict[str, Any]
