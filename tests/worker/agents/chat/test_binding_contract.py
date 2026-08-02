"""chat 도구가 부르는 REST API 자리를 계약의 바인딩으로 검증한다."""

from __future__ import annotations

import re
from typing import Any

from tests.support.contract import agent_tools
from tracer_agent.shared.agents.chat.tools.bindings import (
    LOCAL_TOOL_NAMES,
    TOOL_BINDINGS,
    ToolBinding,
    fill_path,
)
from tracer_agent.shared.agents.chat.tools.surface import CONFIRM_SURFACE, READ_SURFACES, tool_surface
from tracer_agent.worker.agents.chat.tools import chat_tool_declarations, tool_arg_names

# 사용자 범위는 진입점을 만들 때 묶으므로 모델이 채우는 자리에는 이 이름이 설 수 없다.
SCOPE_ARG = re.compile(r"^(user|owner|principal|account|scope|tenant)(_?id)?$", re.IGNORECASE)


def _bindings() -> Any:
    return agent_tools("chat")["bindings"]


def _expected(binding: ToolBinding) -> dict[str, Any]:
    return {
        "method": binding.method,
        "path": binding.path,
        "pathArgs": list(binding.path_args),
        "query": dict(binding.query),
        "body": dict(binding.body),
        "bodyConstants": dict(binding.body_constants),
    }


def test_바인딩_표가_계약과_바이트로_같다() -> None:
    golden = _bindings()

    assert set(TOOL_BINDINGS) == set(golden["bindings"])
    assert list(LOCAL_TOOL_NAMES) == golden["local"]
    for name, binding in TOOL_BINDINGS.items():
        assert _expected(binding) == golden["bindings"][name]


def test_바인딩과_로컬_도구를_합치면_도구_표면_전체다() -> None:
    assert set(TOOL_BINDINGS) | set(LOCAL_TOOL_NAMES) == set(chat_tool_declarations())
    assert not set(TOOL_BINDINGS) & set(LOCAL_TOOL_NAMES)


def test_되읽는_표면의_도구는_본문을_싣지_않는다() -> None:
    for name, binding in TOOL_BINDINGS.items():
        if tool_surface(name) not in READ_SURFACES:
            continue
        assert binding.method == "GET"
        assert not binding.body and not binding.body_constants


def test_확인을_받는_표면의_도구는_GET_을_쓰지_않는다() -> None:
    for name, binding in TOOL_BINDINGS.items():
        if tool_surface(name) == CONFIRM_SURFACE:
            assert binding.method != "GET"


def test_도구_인자마다_경로_쿼리_본문_중_한_자리가_있다() -> None:
    for name, binding in TOOL_BINDINGS.items():
        required, optional = tool_arg_names(name)
        placed = [*binding.path_args, *binding.query, *binding.body]

        assert len(placed) == len(set(placed))
        assert set(placed) == set(required) | set(optional)
        assert binding.placeholders() == binding.path_args


def test_사용자_범위를_사칭하는_인자가_어느_자리에도_없다() -> None:
    for name in chat_tool_declarations():
        for arg in (*tool_arg_names(name)[0], *tool_arg_names(name)[1]):
            assert not SCOPE_ARG.match(arg)
    for binding in TOOL_BINDINGS.values():
        wires = [
            *binding.path_args,
            *binding.query.values(),
            *binding.body.values(),
            *binding.body_constants,
        ]
        for wire in wires:
            assert not SCOPE_ARG.match(wire)
    assert SCOPE_ARG.match("userId")
    assert not SCOPE_ARG.match("taskId")


def test_경로_자리를_채울_때_인자_값을_이스케이프한다() -> None:
    assert fill_path(TOOL_BINDINGS["get_timeline"], {"taskId": "a/b"}) == "/api/v1/tasks/a%2Fb/timeline"
