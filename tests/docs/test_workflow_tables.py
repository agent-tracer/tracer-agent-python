"""실행 구조 문서의 액티비티 표가 계약이 소유한 값을 그대로 적는지 검증한다."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from tests.support.contract import workflow_contract

from tracer_agent.worker.agents.chat.steps.converse import CONVERSE_DEADLINE_SHARE
from tracer_agent.worker.agents.chat.steps.load_context import CONTEXT_ATTEMPTS
from tracer_agent.worker.agents.runtime.llm.retry import MAX_RETRIES
from tracer_agent.worker.agents.runtime.llm.standard_agent import (
    CONTEXT_EDITING_KEEP_TOOL_RESULTS,
    CONTEXT_EDITING_TRIGGER_TOKENS,
)

AGENTS_ROOT = Path(__file__).resolve().parents[2] / "src" / "tracer_agent" / "worker" / "agents"

CHAT_DOC = AGENTS_ROOT / "chat" / "README.md"
AGENTS_DOC = AGENTS_ROOT / "README.md"
JOB_DOCS = (
    AGENTS_ROOT / "recipe_scan" / "README.md",
    AGENTS_ROOT / "task_cleanup" / "README.md",
    AGENTS_ROOT / "title_suggestion" / "README.md",
)

# 생성 상한은 종류마다 다르므로 문서마다 자기 종류의 값과 맞춘다.
_JOB_DOC_KIND = {
    AGENTS_ROOT / "recipe_scan" / "README.md": "recipeScan",
    AGENTS_ROOT / "task_cleanup" / "README.md": "taskCleanup",
    AGENTS_ROOT / "title_suggestion" / "README.md": "titleSuggestion",
}


def _kind_generate(doc: Path) -> dict[str, Any]:
    """계약이 그 종류의 생성 활동에 적은 상한이다."""
    declared = _CONTRACT["jobWorkflows"]["perKind"][_JOB_DOC_KIND[doc]]["activities"]
    return next(one for one in declared if one["name"].startswith("generate"))


_ROW = re.compile(r"^\|\s*`(?P<name>\w+)`\s*\|(?P<rest>.+)\|\s*$", re.M)
_SECONDS = re.compile(r"^(\d+)초$")
_RETRIES = re.compile(r"최대 (\d+)회 재시도")

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
    if doc in _JOB_DOC_KIND:
        # 생성만 종류마다 값이 달라 축의 기본값이 아니라 그 종류의 값과 맞춘다.
        declared = {**declared, "generateAgentJob": _kind_generate(doc)}
    rows = _rows(doc)

    assert rows, doc.name
    for name, cells in rows.items():
        assert name in declared, f"{doc.name}: {name}"
        assert _limit(cells) == declared[name]["startToCloseSeconds"], f"{doc.name}: {name}"
        attempts = _attempts(cells)
        if attempts is not None:
            assert attempts == declared[name]["maximumAttempts"], f"{doc.name}: {name}"


@pytest.mark.parametrize("doc", [AGENTS_ROOT / "README.md", CHAT_DOC], ids=lambda doc: doc.parent.name)
def test_문서가_적은_재시도_횟수가_런타임이_정한_값과_같다(doc: Path) -> None:
    written = _RETRIES.findall(doc.read_text(encoding="utf-8"))

    assert written, doc.name
    assert {int(count) for count in written} == {MAX_RETRIES}, doc.name


def _generate_activity(workflow_key: str, name: str) -> dict[str, Any]:
    return _declared(workflow_key)[name]


# 문서는 세는 수를 낱말로 적으므로 그 낱말과 값을 잇는 자리를 여기 하나 둔다.
_KOREAN_COUNTS = {1: "한 번", 2: "두 번", 3: "세 번", 4: "네 번", 5: "다섯 번"}

# 문서가 소유자 없이 적으면 조용히 낡으므로 표 밖에 적힌 값도 자기 소유자와 맨다.
_CITATIONS: tuple[tuple[Path, str, str], ...] = (
    (AGENTS_DOC, r"([\d,]+) token부터", f"{CONTEXT_EDITING_TRIGGER_TOKENS:,}"),
    (CHAT_DOC, r"([\d,]+) token부터", f"{CONTEXT_EDITING_TRIGGER_TOKENS:,}"),
    (AGENTS_DOC, r"최근 (\d+)개를 유지한다", str(CONTEXT_EDITING_KEEP_TOOL_RESULTS)),
    (CHAT_DOC, r"최근 (\d+)개를 보존한다", str(CONTEXT_EDITING_KEEP_TOOL_RESULTS)),
    (CHAT_DOC, r"연결 계열 오류만 (.+?)까지 다시 걸고", _KOREAN_COUNTS[CONTEXT_ATTEMPTS]),
    (CHAT_DOC, r"실행 데드라인의 (\d+)%", str(round(CONVERSE_DEADLINE_SHARE * 100))),
)


@pytest.mark.parametrize(
    ("doc", "pattern", "expected"),
    _CITATIONS,
    ids=[f"{doc.parent.name}-{index}" for index, (doc, _, _) in enumerate(_CITATIONS)],
)
def test_문서가_표_밖에_적은_값이_소유자와_같다(doc: Path, pattern: str, expected: str) -> None:
    written = re.findall(pattern, doc.read_text(encoding="utf-8"))

    assert written, f"{doc.name}: {pattern}"
    assert set(written) == {expected}, f"{doc.name}: {pattern}"


@pytest.mark.parametrize("doc", [CHAT_DOC, *JOB_DOCS], ids=lambda doc: doc.parent.name)
def test_비고가_적은_하트비트_상한이_계약이_적은_값과_같다(doc: Path) -> None:
    key = "chatExecution" if doc == CHAT_DOC else "agentJob"
    name = "generateChatExecution" if doc == CHAT_DOC else "generateAgentJob"
    declared = _generate_activity(key, name)
    written = re.findall(r"(\d+)초 heartbeat", doc.read_text(encoding="utf-8"))

    assert written, doc.name
    assert set(written) == {str(declared["heartbeatTimeoutSeconds"])}, doc.name


@pytest.mark.parametrize("doc", JOB_DOCS, ids=lambda doc: doc.parent.name)
def test_비고가_적은_전체_상한이_그_종류의_값과_같다(doc: Path) -> None:
    declared = _kind_generate(doc)
    written = re.findall(r"(\d+)분 schedule-to-close", doc.read_text(encoding="utf-8"))

    assert written, doc.name
    assert set(written) == {str(declared["scheduleToCloseSeconds"] // 60)}, doc.name
