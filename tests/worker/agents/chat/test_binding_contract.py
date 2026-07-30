"""chat 도구가 부르는 REST API 자리를 계약의 바인딩으로 검증한다."""

from __future__ import annotations

import re
from typing import Any

from tests.support.contract import agent_spec
from tracer_agent.shared.agents.chat.tools.bindings import (
    GATE_CONFIRM,
    GATE_NONE,
    LOCAL_TOOL_NAMES,
    TOOL_BINDINGS,
    ToolBinding,
    fill_path,
)
from tracer_agent.worker.agents.chat.tools import TOOL_SPECS

# 사용자 범위는 진입점을 만들 때 묶으므로 모델이 채우는 자리에는 이 이름이 설 수 없다.
SCOPE_ARG = re.compile(r"^(user|owner|principal|account|scope|tenant)(_?id)?$", re.IGNORECASE)


def _bindings() -> Any:
    return agent_spec("chat")["bindings"]


def _expected(binding: ToolBinding) -> dict[str, Any]:
    return {
        "method": binding.method,
        "path": binding.path,
        "gate": binding.gate,
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
    assert set(TOOL_BINDINGS) | set(LOCAL_TOOL_NAMES) == set(TOOL_SPECS)
    assert not set(TOOL_BINDINGS) & set(LOCAL_TOOL_NAMES)


def test_게이트가_도구_계약의_mutation과_어긋나지_않는다() -> None:
    tools = agent_spec("chat")["tools"]["tools"]

    for name, binding in TOOL_BINDINGS.items():
        assert binding.gate == (GATE_CONFIRM if tools[name]["mutation"] else GATE_NONE)
        # remember_fact는 gate none이지만 GET이 아닌 유일한 예외다: 확인 없이 즉시 쓰는 PUT을 쓴다.
        if binding.method == "GET":
            assert binding.gate == GATE_NONE
            assert not binding.body and not binding.body_constants
        if binding.gate == GATE_CONFIRM:
            assert binding.method != "GET"


def test_도구_인자마다_경로_쿼리_본문_중_한_자리가_있다() -> None:
    for name, binding in TOOL_BINDINGS.items():
        spec = TOOL_SPECS[name]
        placed = [*binding.path_args, *binding.query, *binding.body]

        assert len(placed) == len(set(placed))
        assert set(placed) == set(spec.required) | set(spec.optional)
        assert binding.placeholders() == binding.path_args


def test_사용자_범위를_사칭하는_인자가_어느_자리에도_없다() -> None:
    for spec in TOOL_SPECS.values():
        for arg in (*spec.required, *spec.optional):
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
