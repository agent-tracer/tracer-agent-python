"""실행 구조 문서의 액티비티 표가 계약이 소유한 값을 그대로 적는지 검증한다."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from tests.support.contract import workflow_contract

AGENTS_ROOT = Path(__file__).resolve().parents[2] / "src" / "tracer_agent" / "worker" / "agents"

CHAT_DOC = AGENTS_ROOT / "chat" / "README.md"
JOB_DOCS = (
    AGENTS_ROOT / "recipe_scan" / "README.md",
    AGENTS_ROOT / "task_cleanup" / "README.md",
    AGENTS_ROOT / "title_suggestion" / "README.md",
)

_ROW = re.compile(r"^\|\s*`(?P<name>\w+)`\s*\|(?P<rest>.+)\|\s*$", re.M)
_SECONDS = re.compile(r"^(\d+)초$")

_CONTRACT = workflow_contract("queues.yaml")
_WORKFLOWS = {
    **_CONTRACT["workflows"],
    "agentJob": _CONTRACT["jobWorkflows"]["singleKind"]["agentJob"],
}


def _declared(*workflow_keys: str) -> dict[str, dict[str, Any]]:
    return {activity["name"]: activity for key in workflow_keys for activity in _WORKFLOWS[key]["activities"]}


def _rows(doc: Path) -> dict[str, tuple[str, ...]]:
    """문서의 액티비티 표에서 계약이 소유한 칸만 이름에 매어 낸다."""
    found: dict[str, tuple[str, ...]] = {}
    for match in _ROW.finditer(doc.read_text(encoding="utf-8")):
        cells = tuple(cell.strip().strip("`") for cell in match.group("rest").split("|"))
        if any(_SECONDS.match(cell) for cell in cells):
            found[match.group("name")] = cells
    return found


def _limit(cells: tuple[str, ...]) -> int:
    return next(int(seconds.group(1)) for cell in cells if (seconds := _SECONDS.match(cell)))


def _attempts(cells: tuple[str, ...]) -> int | None:
    after = cells[cells.index(next(cell for cell in cells if _SECONDS.match(cell))) + 1]
    return int(after) if after.isdigit() else None


@pytest.mark.parametrize(
    ("doc", "workflow_keys"),
    [
        (CHAT_DOC, ("chatThread", "chatExecution")),
        *((doc, ("agentJob",)) for doc in JOB_DOCS),
    ],
    ids=lambda value: value.parent.name if isinstance(value, Path) else "",
)
def test_문서의_액티비티_표가_계약이_적은_상한과_시도_수를_적는다(
    doc: Path, workflow_keys: tuple[str, ...]
) -> None:
    declared = _declared(*workflow_keys)
    rows = _rows(doc)

    assert rows, doc.name
    for name, cells in rows.items():
        assert name in declared, f"{doc.name}: {name}"
        assert _limit(cells) == declared[name]["startToCloseSeconds"], f"{doc.name}: {name}"
        attempts = _attempts(cells)
        if attempts is not None:
            assert attempts == declared[name]["maximumAttempts"], f"{doc.name}: {name}"
