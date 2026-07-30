"""잡 종류의 wire 값과 이 서비스의 에이전트 이름 사이를 옮기고 그 실행 주체를 소유한다."""

from __future__ import annotations

from .jobs_spec import AgentJobKind

TEMPORAL_EXECUTOR = "temporal"
LOCAL_EXECUTOR = "local"

# 워크플로가 도는 잡과 플러그인이 궤적을 넘기는 잡을 이 값으로 가른다.
JOB_EXECUTOR: dict[str, str] = {
    "title.suggestion": TEMPORAL_EXECUTOR,
    "recipe.scan": TEMPORAL_EXECUTOR,
    "task.cleanup": TEMPORAL_EXECUTOR,
    "rule.generation": LOCAL_EXECUTOR,
}

JOB_KINDS: tuple[str, ...] = tuple(JOB_EXECUTOR)

AGENT_KIND_BY_WIRE: dict[str, AgentJobKind] = {
    "title.suggestion": "title-suggestion",
    "recipe.scan": "recipe-scan",
    "task.cleanup": "task-cleanup",
}

WIRE_BY_AGENT_KIND: dict[AgentJobKind, str] = {
    agent_kind: wire for wire, agent_kind in AGENT_KIND_BY_WIRE.items()
}


def wire_kind(agent_kind: AgentJobKind) -> str:
    """에이전트 이름을 브라우저와 계약이 쓰는 잡 종류 값으로 옮긴다."""
    return WIRE_BY_AGENT_KIND[agent_kind]


def runs_locally(kind: str) -> bool:
    """로컬 실행기가 가져가는 잡은 워크플로로 보내지 않고 원장에만 세운다."""
    return JOB_EXECUTOR[kind] == LOCAL_EXECUTOR
