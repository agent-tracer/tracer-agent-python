"""잡 종류의 wire 값과 이 서비스의 에이전트 이름 사이를 옮기고 그 실행 주체를 소유한다."""

from __future__ import annotations

from enum import StrEnum


class JobExecutor(StrEnum):
    """잡을 실제로 실행하는 주체이며 지금은 모두 워크플로가 실행한다."""

    TEMPORAL = "temporal"


class AgentJobKind(StrEnum):
    """이 서비스가 그래프로 실행하는 잡 종류이며 값은 그 잡을 맡은 에이전트의 이름이다."""

    wire: str

    def __new__(cls, agent_name: str, wire: str) -> AgentJobKind:
        kind = str.__new__(cls, agent_name)
        kind._value_ = agent_name
        kind.wire = wire
        return kind

    TITLE_SUGGESTION = ("title-suggestion", "title.suggestion")
    RECIPE_SCAN = ("recipe-scan", "recipe.scan")
    TASK_CLEANUP = ("task-cleanup", "task.cleanup")

    @classmethod
    def of_wire(cls, wire: str) -> AgentJobKind:
        """브라우저와 계약이 쓰는 잡 종류 값을 이 서비스의 에이전트 이름으로 옮긴다."""
        return _KIND_BY_WIRE[wire]


_KIND_BY_WIRE: dict[str, AgentJobKind] = {kind.wire: kind for kind in AgentJobKind}

# 계약과 브라우저가 쓰는 잡 종류 값이며 이 순서가 정본이다.
JOB_KINDS: tuple[str, ...] = tuple(kind.wire for kind in AgentJobKind)
# 이 서비스가 그 잡을 맡긴 에이전트의 이름이다.
JOB_AGENT_NAMES: tuple[str, ...] = tuple(kind.value for kind in AgentJobKind)
JOB_KIND_BY_AGENT_NAME: dict[str, str] = {kind.value: kind.wire for kind in AgentJobKind}

JOB_EXECUTOR: dict[str, JobExecutor] = dict.fromkeys(JOB_KINDS, JobExecutor.TEMPORAL)
