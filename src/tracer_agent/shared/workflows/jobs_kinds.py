"""잡 종류의 wire 값과 이 서비스의 에이전트 이름 사이를 옮기고 그 실행 주체를 소유한다."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .jobs_spec import AgentJobKind

# 계약 저장소는 배포 이미지의 서비스 루트에 함께 실린다.
_JOB_KINDS_PATH = Path(__file__).resolve().parents[4] / "contract" / "wire" / "job.kinds.json"


@lru_cache(maxsize=1)
def lease_ttl_ms() -> int:
    """리스가 이만큼 살아 있고 하트비트가 이보다 잦아야 다른 실행기가 같은 잡을 가져가지 않는다."""
    document = json.loads(_JOB_KINDS_PATH.read_text(encoding="utf-8"))
    return int(document["lease"]["ttlMs"])


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
