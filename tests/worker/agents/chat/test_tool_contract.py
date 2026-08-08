"""chat 도구 표면과 모델이 보는 스키마를 계약으로 검증한다."""

from __future__ import annotations

from typing import Any

from langchain_core.utils.function_calling import convert_to_openai_tool

from tests.support.contract import agent_tools, conformance_case
from tracer_agent.shared.agents.envelope.tools import chat_tool_descriptions
from tracer_agent.worker.agents.chat.tools import (
    AGENT_READ_TOOL_NAMES,
    ARGS_MODELS,
    MEMORY_TOOL_NAMES,
    READ_TOOL_NAMES,
    TOOL_FAILED,
    WRITE_TOOL_NAMES,
    chat_tool_registry,
    tool_arg_names,
)


def _contract() -> Any:
    return agent_tools("chat")


def _langchain_tools(descriptions: dict[str, str] | None = None) -> dict[str, Any]:
    registry = chat_tool_registry(chat_tool_descriptions() if descriptions is None else descriptions)
    return {tool.name: tool for tool in registry.langchain_tools()}


def _variants(field: dict[str, Any]) -> list[dict[str, Any]]:
    return [one for one in field.get("anyOf", [field]) if one.get("type") != "null"]


def test_모델에게_여는_도구_이름이_계약과_같다() -> None:
    assert set(_langchain_tools()) == set(_contract()["tools"])


def test_표면_분할이_적합성이_적은_목록과_같다() -> None:
    declared = conformance_case("chat.tools")["tools"]

    assert set(READ_TOOL_NAMES) == set(declared["read"])
    assert set(AGENT_READ_TOOL_NAMES) == set(declared["agentRead"])
    assert set(MEMORY_TOOL_NAMES) == set(declared["memory"])
    assert set(WRITE_TOOL_NAMES) == set(declared["confirm"])


def test_한_도구가_두_표면에_함께_서지_않는다() -> None:
    tools = _contract()["tools"]
    grouped = [*READ_TOOL_NAMES, *AGENT_READ_TOOL_NAMES, *MEMORY_TOOL_NAMES, *WRITE_TOOL_NAMES]

    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(tools)


def test_표준_tool이_runtime을_숨기고_계약이_적은_인자만_노출한다() -> None:
    for name, tool in _langchain_tools().items():
        schema = tool.tool_call_schema
        required, optional = tool_arg_names(name)

        assert set(schema.get("required", [])) == set(required)
        assert set(schema["properties"]) == set(required) | set(optional)
        assert "runtime" not in schema["properties"]


def test_모델이_보는_스키마는_계약이_만든_인자_모델_그대로다() -> None:
    # 실행 기계로 옮긴 뒤에도 별칭과 설명과 상한이 계약 판 그대로 모델에게 가야 한다.
    descriptions = chat_tool_descriptions()

    for name, tool in _langchain_tools().items():
        assert tool.description == descriptions[name]
        assert tool.args_schema == ARGS_MODELS[name].model_json_schema()


def test_계약이_금지한_여분_인자가_모델이_받는_도구_선언에도_적힌다() -> None:
    for tool in _langchain_tools().values():
        declared = convert_to_openai_tool(tool)["function"]["parameters"]

        assert declared["additionalProperties"] is False


def test_설명을_싣지_않은_봉투는_도구_이름을_설명으로_쓴다() -> None:
    tools = _langchain_tools({})

    assert {name for name, tool in tools.items() if tool.description != name} == set()


def test_도구_실패_문구가_계약과_같다() -> None:
    assert _contract()["failures"]["toolFailed"] == TOOL_FAILED


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
        ("propose_rule_write", "expectation"),
        ("propose_rule_write", "expectation"),
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
