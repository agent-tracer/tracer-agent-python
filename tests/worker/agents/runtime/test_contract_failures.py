"""실패 문구가 코드 복사본이 아니라 계약에서 오는지 검증한다(네트워크 없음)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracer_agent.shared.agents.chat.tools.surface import chat_tool_failure_text
from tracer_agent.worker.agents.chat.tools.registry import TOOL_FAILED
from tracer_agent.worker.agents.recipe_scan.failures import WORKER_FAILED as RECIPE_WORKER_FAILED
from tracer_agent.worker.agents.shared.contract_failures import (
    TOOL_FAILED_KEY,
    WORKER_FAILED_KEY,
    failure_text,
)
from tracer_agent.worker.agents.shared.contract_prompt_source import (
    CONTRACT_ROOT,
    ContractPromptUnavailable,
)
from tracer_agent.worker.agents.task_cleanup.failures import WORKER_FAILED as CLEANUP_WORKER_FAILED


def _declared(agent: str, key: str) -> str:
    contract = json.loads(Path(CONTRACT_ROOT / "agent" / agent / "tool.json").read_text(encoding="utf-8"))
    return str(contract["failures"][key])


def test_대화의_도구_실패_문구가_계약과_바이트로_같다() -> None:
    assert _declared("chat", TOOL_FAILED_KEY) == TOOL_FAILED


@pytest.mark.parametrize(
    ("agent", "text"),
    [("recipe-scan", RECIPE_WORKER_FAILED), ("task-cleanup", CLEANUP_WORKER_FAILED)],
)
def test_조사_실패_문구가_계약과_바이트로_같다(agent: str, text: str) -> None:
    assert text == _declared(agent, WORKER_FAILED_KEY)


def test_계약이_선언하지_않은_문구는_거절한다() -> None:
    with pytest.raises(ContractPromptUnavailable):
        failure_text("recipe-scan", "notDeclared")
    with pytest.raises(ValueError, match="no failure text"):
        chat_tool_failure_text("notDeclared")
