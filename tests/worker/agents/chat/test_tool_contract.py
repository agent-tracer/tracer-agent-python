"""chat 도구 표면과 예산을 계약으로 검증한다."""

from __future__ import annotations

from typing import Any

from tests.support.contract import agent_spec
from tracer_agent.worker.agents.chat.tools import (
    ARG_DESCRIPTIONS,
    MEMORY_TOOL_NAMES,
    READ_TOOL_NAMES,
    TOOL_FAILED,
    TOOL_SPECS,
    WRITE_TOOL_NAMES,
    EnumArg,
    NumberArg,
    ToolSpec,
    build_chat_registry,
)


def _contract() -> Any:
    return agent_spec("chat")["tools"]


def _langchain_tools() -> dict[str, Any]:
    registry = build_chat_registry(None, [], {}, agent_name="chat")
    return {tool.name: tool for tool in registry.langchain_tools()}


def _expected(spec: ToolSpec) -> dict[str, Any]:
    expected: dict[str, Any] = {
        "required": list(spec.required),
        "optional": list(spec.optional),
        "mutation": spec.mutation,
    }
    for arg, constraint in spec.constraints.items():
        if isinstance(constraint, EnumArg):
            expected[arg] = {"values": list(constraint.values)}
        elif isinstance(constraint, NumberArg):
            expected[arg] = {
                "default": constraint.default,
                "min": constraint.minimum,
                "max": constraint.maximum,
            }
    return expected


def test_모델에게_여는_도구_이름이_계약과_같다() -> None:
    assert set(_langchain_tools()) == set(_contract()["tools"])
    assert set(TOOL_SPECS) == set(_contract()["tools"])


def test_mutation_분할이_계약과_같다() -> None:
    tools = _contract()["tools"]
    mutation = {name for name, spec in tools.items() if spec["mutation"]}

    assert set(WRITE_TOOL_NAMES) == mutation
    assert set(READ_TOOL_NAMES) | set(MEMORY_TOOL_NAMES) == set(tools) - mutation
    assert set(MEMORY_TOOL_NAMES) == {"recall_facts", "remember_fact"}


def test_각_도구의_인자_명세가_계약과_바이트로_같다() -> None:
    tools = _contract()["tools"]

    for name, spec in TOOL_SPECS.items():
        assert _expected(spec) == tools[name]


def test_표준_tool이_runtime을_숨기고_계약이_적은_인자만_노출한다() -> None:
    tools = _langchain_tools()
    contract = _contract()["tools"]

    for name, tool in tools.items():
        schema = tool.tool_call_schema
        spec = contract[name]
        assert set(schema.get("required", [])) == set(spec["required"])
        assert set(schema["properties"]) == set(spec["required"] + spec["optional"])
        assert "runtime" not in schema["properties"]


def test_도구_실패_문구가_계약과_같다() -> None:
    assert _contract()["failures"]["toolFailed"] == TOOL_FAILED


async def test_읽기_진입점이_없는_도구가_계약_문구로_실패를_알린다() -> None:
    registry = build_chat_registry(None, [], {}, agent_name="chat")
    tool = next(t for t in registry.langchain_tools() if t.name == "search_tasks")

    text = await tool.coroutine()

    assert "search_tasks" in text
    assert "{" not in text
    # chat에는 rationale이 없어 다른 에이전트와 달리 사용자에게 직접 말하라고 시킨다.
    assert "rationale" not in text


def test_인자_설명이_계약과_같다() -> None:
    assert _contract()["argDescriptions"] == ARG_DESCRIPTIONS


def test_인자_설명이_모델이_보는_스키마에_실린다() -> None:
    tools = _langchain_tools()
    contract = _contract()["argDescriptions"]

    for name, tool in tools.items():
        properties = tool.tool_call_schema["properties"]
        shown = {arg: field.get("description") for arg, field in properties.items()}
        assert shown == contract[name]
