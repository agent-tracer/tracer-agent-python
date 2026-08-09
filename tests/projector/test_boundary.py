"""배경 작업 셋이 배포 단위 하나에만 배선되는지 검증한다."""

from __future__ import annotations

import ast
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = SERVICE_ROOT / "src" / "tracer_agent" / "api"
PROJECTOR_ROOT = SERVICE_ROOT / "src" / "tracer_agent" / "projector"

# 배경 작업 셋을 세우는 이름이며 이 셋을 맡는 배포 단위는 agent-projector 하나다.
BACKGROUND_JOBS = frozenset({"SearchOutboxDrainScheduler", "LedgerEventConsumer", "OpenSearchIndexAdmin"})


def imported_names(root: Path) -> set[str]:
    """디렉터리의 모든 모듈이 import 로 들여온 이름을 모은다."""
    names: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.update(alias.name for alias in node.names)
    return names


def test_접수_창구는_배경_작업을_배선하지_않는다() -> None:
    assert imported_names(API_ROOT) & BACKGROUND_JOBS == set()


def test_배경_작업_셋을_새_배포_단위가_모두_배선한다() -> None:
    assert imported_names(PROJECTOR_ROOT) >= BACKGROUND_JOBS
