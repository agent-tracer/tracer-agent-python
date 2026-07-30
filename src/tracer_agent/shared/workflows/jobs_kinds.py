"""잡 종류의 wire 값과 이 서비스의 에이전트 이름 사이를 옮기고 그 실행 주체를 소유한다."""

from __future__ import annotations

from .jobs_spec import AgentJobKind

# 워크플로가 도는 잡이며 플러그인이 궤적을 넘기는 잡과 이 값으로 갈린다.
JOB_EXECUTOR = "temporal"

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
