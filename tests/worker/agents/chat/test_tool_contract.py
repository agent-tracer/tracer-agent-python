"""chat 도구 표면과 예산을 계약으로 검증한다."""

from __future__ import annotations

from typing import Any

from tests.support.contract import agent_tools
from tracer_agent.worker.agents.chat.tools import (
    AGENT_READ_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    READ_TOOL_NAMES,
    TOOL_FAILED,
    WRITE_TOOL_NAMES,
    build_chat_registry,
    tool_arg_names,
)


def _contract() -> Any:
    return agent_tools("chat")


def _langchain_tools() -> dict[str, Any]:
    registry = build_chat_registry(None, [], {}, agent_name="chat")
    return {tool.name: tool for tool in registry.langchain_tools()}


def _variants(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [one for one in field.get("anyOf", [field]) if one.get("type") != "null"]


def test_모델에게_여는_도구_이름이_계약과_같다() -> None:
    assert set(_langchain_tools()) == set(_contract()["tools"])


def test_mutation_분할이_계약과_같다() -> None:
    tools = _contract()["tools"]
    mutation = {name for name, spec in tools.items() if spec["mutation"]}

    assert set(WRITE_TOOL_NAMES) == mutation
    assert set(READ_TOOL_NAMES) | set(AGENT_READ_TOOL_NAMES) | set(MEMORY_TOOL_NAMES) == set(tools) - mutation
    assert set(MEMORY_TOOL_NAMES) == {"recall_facts"}
    assert set(AGENT_READ_TOOL_NAMES) == {"get_job"}


def test_표준_tool이_runtime을_숨기고_계약이_적은_인자만_노출한다() -> None:
    for name, tool in _langchain_tools().items():
        schema = tool.tool_call_schema
        required, optional = tool_arg_names(name)

        assert set(schema.get("required", [])) == set(required)
        assert set(schema["properties"]) == set(required) | set(optional)
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


def test_인자_설명이_모델이_보는_스키마에_실린다() -> None:
    tools = _contract()["tools"]

    for name, tool in _langchain_tools().items():
        properties = tool.tool_call_schema["properties"]
        shown = {arg: field.get("description") for arg, field in properties.items()}

        assert shown == {arg: declared["description"] for arg, declared in tools[name]["args"].items()}


def test_계약이_object라_적은_인자는_모델이_객체로_보낸다() -> None:
    tools = _contract()["tools"]
    declared = {
        (name, arg)
        for name, tool in tools.items()
        for arg, field in tool["args"].items()
        if field["type"] == "object"
    }

    assert declared == {
        ("enqueue_job", "input"),
        ("create_rule", "expectation"),
        ("update_rule", "expectation"),
    }
    for name, arg in declared:
        variant = _variants(_langchain_tools()[name].tool_call_schema["properties"][arg])[0]
        assert variant["type"] == "object"


def test_열거와_수치와_배열의_상한이_모델이_보는_스키마에_실린다() -> None:
    tools = _contract()["tools"]

    for name, tool in _langchain_tools().items():
        properties = tool.tool_call_schema["properties"]
        for arg, declared in tools[name]["args"].items():
            variant = _variants(properties[arg])[0]
            if declared["type"] == "enum":
                assert variant.get("enum", [variant.get("const")]) == declared["values"]
            if declared["type"] == "integer":
                assert (variant["minimum"], variant["maximum"]) == (declared["min"], declared["max"])
            if declared["type"] == "array":
                assert variant["maxItems"] == declared["maxItems"]
