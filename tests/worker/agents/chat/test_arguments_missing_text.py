"""빠진 인자를 실은 호출에 모델이 포기 지시 대신 고쳐 부르라는 문장을 읽는지 검증한다."""

from __future__ import annotations

import json

from tracer_agent.shared.agents.chat.tools.surface import chat_tool_failure_text
from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT
from tracer_agent.worker.agents.chat.tools.registry import chat_tool_arguments_missing
from tracer_agent.worker.agents.shared.contract_failures import ARGUMENTS_MISSING_KEY

DECLARED = json.loads((CONTRACT_ROOT / "agent" / "chat" / "tool.json").read_text(encoding="utf-8"))


def test_고칠_수_있는_실수에는_포기_지시가_섞이지_않는다() -> None:
    text = chat_tool_arguments_missing("propose_task_write", "update", ("taskId",))

    assert "propose_task_write" in text
    assert "taskId" in text
    assert "update" in text
    assert "Do not call it again" not in text


def test_두_문구는_계약이_서로_다른_칸에_갖는다() -> None:
    assert DECLARED["argumentRejection"]["modelText"] == f"failures.{ARGUMENTS_MISSING_KEY}"
    assert chat_tool_failure_text(ARGUMENTS_MISSING_KEY) != chat_tool_failure_text("toolFailed")
