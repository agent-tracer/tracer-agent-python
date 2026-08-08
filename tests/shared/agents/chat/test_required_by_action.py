"""계약의 표와 손으로 적은 계획이 같은 규칙을 각자 아는 동안 둘이 어긋나지 않는지 검증한다."""

from __future__ import annotations

import json

import pytest

from tracer_agent.shared.agents.chat.surface.tool_calls import (
    ChatToolArgsInvalid,
    missing_action_arguments,
    plan_chat_tool_call,
)
from tracer_agent.shared.agents.shared.contract_root import CONTRACT_ROOT
from tracer_agent.shared.agents.shared.json_view import JsonObject

DECLARED = json.loads((CONTRACT_ROOT / "agent" / "chat" / "tool.json").read_text(encoding="utf-8"))

FILLED_TEXT = "x"


def _combinations() -> list[tuple[str, str, str]]:
    return [
        (name, action, missing)
        for name, tool in DECLARED["tools"].items()
        for action, required in (tool.get("requiredByAction") or {}).items()
        for missing in required
    ]


def _args(name: str, action: str, missing: str) -> JsonObject:
    tool = DECLARED["tools"][name]
    required = tool["requiredByAction"][action]
    filled: JsonObject = {"action": action}
    for one in required:
        if one == missing:
            continue
        filled[one] = [FILLED_TEXT] if tool["args"][one]["type"] == "array" else FILLED_TEXT
    return filled


@pytest.mark.parametrize(("name", "action", "missing"), _combinations())
def test_계약의_표가_적은_인자는_계획도_없이는_세우지_못한다(name: str, action: str, missing: str) -> None:
    # 표를 고쳐도 계획은 따라오지 않으므로 표가 계획보다 넓어지는 순간을 이 자리가 잡는다.
    args = _args(name, action, missing)

    assert missing_action_arguments(name, args) == (missing,)
    with pytest.raises(ChatToolArgsInvalid):
        plan_chat_tool_call(name, args)
