"""승인된 도구 호출의 요청 인자와 결과 문장을 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import agent_tools
from tracer_agent.shared.agents.chat.surface.confirmations import PROPOSAL_NOTE
from tracer_agent.shared.agents.chat.surface.tool_calls import (
    CONFIRMABLE_TOOLS,
    ChatToolArgsInvalid,
    plan_chat_tool_call,
)
from tracer_agent.shared.agents.chat.tools.surface import CONFIRM_SURFACE, tool_names_on


def test_확인_대기_안내가_계약과_같다() -> None:
    assert agent_tools("chat")["proposalNote"] == PROPOSAL_NOTE


def test_확인을_받는_표면의_도구만_계획을_갖는다() -> None:
    contract = agent_tools("chat")["tools"]
    confirmed = {name for name, spec in contract.items() if spec["surface"] == CONFIRM_SURFACE}

    assert confirmed == CONFIRMABLE_TOOLS
    assert set(tool_names_on(CONFIRM_SURFACE)) == CONFIRMABLE_TOOLS


def test_필수_인자가_없으면_계획을_세우지_않는다() -> None:
    with pytest.raises(ChatToolArgsInvalid):
        plan_chat_tool_call("archive_task", {})


def test_고칠_것이_없는_갱신은_계획을_세우지_않는다() -> None:
    with pytest.raises(ChatToolArgsInvalid):
        plan_chat_tool_call("update_task", {"taskId": "task-1"})


def test_갱신은_바뀌는_자리만_실어_보내고_문장에_적는다() -> None:
    call = plan_chat_tool_call("update_task", {"taskId": "task-1", "status": "completed"})

    assert call.args == {"taskId": "task-1", "status": "completed"}
    assert call.describe(None) == "Updated task task-1: status=completed."


def test_규칙_기대는_객체_그대로_계약이_적은_이름으로_싣는다() -> None:
    call = plan_chat_tool_call(
        "create_rule",
        {
            "taskId": "task-1",
            "anchorEventId": "event-1",
            "name": "규칙",
            "expectation": {"kind": "absent"},
        },
    )

    assert call.args["expect"] == {"kind": "absent"}
    assert call.describe(None) == 'Created rule "규칙" on task task-1.'


def test_객체가_아닌_기대는_계획을_세우지_않는다() -> None:
    with pytest.raises(ChatToolArgsInvalid):
        plan_chat_tool_call(
            "create_rule",
            {"taskId": "t", "anchorEventId": "e", "name": "n", "expectation": "규칙 아님"},
        )


def test_태그_목록은_계약이_선언한_배열_그대로_간다() -> None:
    call = plan_chat_tool_call("set_task_tags", {"taskId": "task-1", "tagIds": [" tag-1 ", "tag-2"]})

    assert call.args["tagIds"] == ["tag-1", "tag-2"]
    assert call.describe(None) == "Set 2 tag(s) on task task-1."


def test_태그_목록이_배열이_아니면_계획을_세우지_않는다() -> None:
    with pytest.raises(ChatToolArgsInvalid):
        plan_chat_tool_call("set_task_tags", {"taskId": "task-1", "tagIds": "tag-1"})


def test_접수한_잡의_문장은_응답의_원장_행을_인용한다() -> None:
    call = plan_chat_tool_call("enqueue_job", {"kind": "recipe.scan", "input": {"taskId": "task-1"}})

    assert call.args == {"kind": "recipe.scan", "input": {"taskId": "task-1"}}
    assert call.describe({"job": {"id": "j1", "status": "pending"}}) == (
        "Enqueued recipe.scan job j1 (status: pending)."
    )


def test_객체가_아닌_잡_입력은_계획을_세우지_않는다() -> None:
    with pytest.raises(ChatToolArgsInvalid):
        plan_chat_tool_call("enqueue_job", {"kind": "recipe.scan", "input": '{"taskId":"task-1"}'})


def test_재평가_문장은_응답이_센_사건_수를_인용한다() -> None:
    call = plan_chat_tool_call("reevaluate_rule", {"ruleId": "rule-1"})

    assert call.describe({"reevaluated": 3}) == "Reevaluated rule rule-1 over 3 event(s)."
    assert call.describe(None) == "Reevaluated rule rule-1 over 0 event(s)."
