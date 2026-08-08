"""확인 게이트가 여는 도구가 계약의 표면 한 칸에서만 나오는지 검증한다."""

from __future__ import annotations

import pytest

from tracer_agent.shared.agents.chat.surface import tool_calls
from tracer_agent.shared.agents.chat.surface.tool_calls import CONFIRMABLE_TOOLS
from tracer_agent.shared.agents.chat.tools.bindings import TOOL_ACTION_BINDINGS
from tracer_agent.shared.agents.chat.tools.surface import CONFIRM_SURFACE, tool_names_on


def test_확인_도구는_계약의_표면이_정한다() -> None:
    assert frozenset(tool_names_on(CONFIRM_SURFACE)) == CONFIRMABLE_TOOLS


def test_계약에만_있는_도구는_조립_시점에_터진다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_calls, "tool_names_on", lambda _surface: ("remember_fact", "새_도구"))

    with pytest.raises(ValueError, match="missing=새_도구"):
        tool_calls.confirmable_tools()


def test_계획에만_있는_도구는_조립_시점에_터진다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_calls, "tool_names_on", lambda _surface: ("remember_fact",))

    with pytest.raises(ValueError, match="extra="):
        tool_calls.confirmable_tools()


def test_계약이_선언한_action_을_계획이_모두_덮는다() -> None:
    planned = tool_calls.planned_actions()
    for name, actions in TOOL_ACTION_BINDINGS.items():
        assert frozenset(actions) <= planned[name]


def test_계획에_없는_action_은_조립_시점에_터진다(monkeypatch: pytest.MonkeyPatch) -> None:
    thinner = {name: dict(actions) for name, actions in TOOL_ACTION_BINDINGS.items()}
    first = next(iter(TOOL_ACTION_BINDINGS["propose_task_write"].values()))
    thinner["propose_task_write"]["없는_action"] = first
    monkeypatch.setattr(tool_calls, "TOOL_ACTION_BINDINGS", thinner)

    with pytest.raises(ValueError, match="없는_action"):
        tool_calls.confirmable_tools()
