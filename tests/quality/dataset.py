"""골든 사례의 형식과 적재를 소유한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).parent / "golden"

AGENTS = ("recipe-scan", "task-cleanup", "title-suggestion")

_REQUIRED = ("id", "agent", "behavior", "sources", "input", "script", "expect")


@dataclass(frozen=True)
class GoldenCase:
    """한 에이전트의 입력과 검증 가능한 기대 성질을 담은 골든 사례 하나다."""

    id: str
    agent: str
    behavior: str
    sources: dict[str, Any]
    input: dict[str, Any]
    script: dict[str, Any]
    expect: dict[str, Any]


def load_cases(agent: str | None = None) -> list[GoldenCase]:
    """골든 디렉터리의 사례를 파일 이름 순서로 적재한다."""
    cases = [_parse(path) for path in sorted(GOLDEN_DIR.glob("*.json"))]
    return [case for case in cases if agent is None or case.agent == agent]


def _parse(path: Path) -> GoldenCase:
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in _REQUIRED if key not in payload]
    if missing:
        raise ValueError(f"{path.name}: 골든 사례에 {', '.join(missing)} 항목이 없다")
    if payload["agent"] not in AGENTS:
        raise ValueError(f"{path.name}: 알 수 없는 에이전트 {payload['agent']}")
    return GoldenCase(
        id=payload["id"],
        agent=payload["agent"],
        behavior=payload["behavior"],
        sources=payload["sources"],
        input=payload["input"],
        script=payload["script"],
        expect=payload["expect"],
    )
