"""잡 종류의 wire 값과 이 서비스의 에이전트 이름 사이를 옮기고 그 실행 주체를 소유한다."""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
_JOB_KINDS_PATH = Path(__file__).resolve().parents[4] / "contract" / "wire" / "job.kinds.json"


@lru_cache(maxsize=1)
def lease_ttl_ms() -> int:
    """리스가 이만큼 살아 있고 하트비트가 이보다 잦아야 다른 실행기가 같은 잡을 가져가지 않는다."""
    document = json.loads(_JOB_KINDS_PATH.read_text(encoding="utf-8"))
    return int(document["lease"]["ttlMs"])


class JobExecutor(StrEnum):
    """워크플로가 도는 잡과 플러그인이 궤적을 넘기는 잡을 가르는 실행 주체다."""

    TEMPORAL = "temporal"
    LOCAL = "local"


class AgentJobKind(StrEnum):
    """이 서비스가 그래프로 돌리는 잡 종류이며 값은 그 잡을 맡은 에이전트의 이름이다."""

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

JOB_EXECUTOR: dict[str, JobExecutor] = {
    AgentJobKind.TITLE_SUGGESTION.wire: JobExecutor.TEMPORAL,
    AgentJobKind.RECIPE_SCAN.wire: JobExecutor.TEMPORAL,
    AgentJobKind.TASK_CLEANUP.wire: JobExecutor.TEMPORAL,
    "rule.generation": JobExecutor.LOCAL,
}

JOB_KINDS: tuple[str, ...] = tuple(JOB_EXECUTOR)


def runs_locally(kind: str) -> bool:
    """로컬 실행기가 가져가는 잡은 워크플로로 보내지 않고 원장에만 세운다."""
    return JOB_EXECUTOR[kind] is JobExecutor.LOCAL
