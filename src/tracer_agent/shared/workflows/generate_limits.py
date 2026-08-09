"""잡 종류마다의 생성 활동 상한을 계약에서 읽는다."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from ..agents.shared.contract_root import CONTRACT_ROOT
from .jobs_kinds import AgentJobKind

QUEUES_PATH = CONTRACT_ROOT / "workflow" / "queues.yaml"

# 계약이 잡 종류마다 워크플로를 두는 축의 이름으로 적으므로 이 축의 종류와 그 이름을 잇는다.
CONTRACT_KEY: dict[AgentJobKind, str] = {
    AgentJobKind.TITLE_SUGGESTION: "titleSuggestion",
    AgentJobKind.RECIPE_SCAN: "recipeScan",
    AgentJobKind.TASK_CLEANUP: "taskCleanup",
}


@dataclass(frozen=True)
class GenerateLimits:
    """생성 활동 하나를 실을 때 거는 상한과 시도 수다."""

    start_to_close_s: float
    schedule_to_close_s: float
    heartbeat_s: float
    max_attempts: int


@lru_cache(maxsize=1)
def _declared() -> dict[AgentJobKind, GenerateLimits]:
    document: Any = yaml.safe_load(QUEUES_PATH.read_text(encoding="utf-8"))
    per_kind = document["jobWorkflows"]["perKind"]
    return {kind: _limits_of(per_kind[key]) for kind, key in CONTRACT_KEY.items()}


def _limits_of(declared: Any) -> GenerateLimits:
    """그 종류의 활동 가운데 생성 하나를 찾아 상한으로 옮긴다."""
    # 계약이 그 종류의 생성 활동을 적지 않으면 이 축이 상한을 스스로 골라야 하므로 여기서 끊는다.
    activity = next(one for one in declared["activities"] if one["name"].startswith("generate"))
    return GenerateLimits(
        start_to_close_s=float(activity["startToCloseSeconds"]),
        schedule_to_close_s=float(activity["scheduleToCloseSeconds"]),
        heartbeat_s=float(activity["heartbeatTimeoutSeconds"]),
        max_attempts=int(activity["maximumAttempts"]),
    )


def generate_limits(kind: AgentJobKind) -> GenerateLimits:
    """계약이 그 종류에 적은 생성 상한을 낸다."""
    return _declared()[kind]
