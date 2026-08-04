"""계약 저장소가 소유한 값을 대조에 쓰도록 계약의 로더를 감싼다."""

from __future__ import annotations

from typing import Any

import yaml
from contract.conformance.runner.contract import (
    contract_root,
    read_agent_cases,
    read_agent_output,
    read_agent_prompt,
    read_agent_tools,
    read_case,
    read_json,
    read_version,
)

PINNED_VERSION = "0.27.0"


def contract_version() -> str:
    """이 저장소가 향하는 계약의 판이며 고정한 값과 대조한다."""
    return read_version()


def agent_prompt(agent_id: str) -> dict[str, Any]:
    """에이전트 하나의 프롬프트 조각과 템플릿이다."""
    payload: dict[str, Any] = read_agent_prompt(agent_id)
    return payload


def agent_tools(agent_id: str) -> dict[str, Any]:
    """에이전트 하나의 도구와 인자와 상한이다."""
    payload: dict[str, Any] = read_agent_tools(agent_id)
    return payload


def agent_tool(agent_id: str, tool_name: str) -> dict[str, Any]:
    """도구 하나의 선언이며 설명과 인자를 갖는다."""
    declared: dict[str, Any] = agent_tools(agent_id)["tools"][tool_name]
    return declared


def tool_arg(agent_id: str, tool_name: str, arg: str) -> dict[str, Any]:
    """도구 인자 하나의 타입과 상한과 설명이다."""
    declared: dict[str, Any] = agent_tool(agent_id, tool_name)["args"][arg]
    return declared


def tool_arg_partition(agent_id: str, tool_name: str) -> tuple[set[str], set[str]]:
    """도구 하나의 인자 이름을 필수와 선택으로 나눈다."""
    args: dict[str, Any] = agent_tool(agent_id, tool_name)["args"]
    required = {name for name, declared in args.items() if declared["required"]}
    return required, set(args) - required


def tool_descriptions(agent_id: str) -> dict[str, str]:
    """도구 이름마다 모델이 읽을 한 문장이다."""
    return {name: str(tool["description"]) for name, tool in agent_tools(agent_id)["tools"].items()}


def tool_arg_descriptions(agent_id: str) -> dict[str, dict[str, str]]:
    """도구마다 인자 이름과 모델이 읽을 설명이다."""
    return {
        name: {arg: str(declared["description"]) for arg, declared in tool["args"].items()}
        for name, tool in agent_tools(agent_id)["tools"].items()
    }


def agent_output(agent_id: str) -> dict[str, Any]:
    """에이전트 하나의 구조화 출력 스키마다."""
    payload: dict[str, Any] = read_agent_output(agent_id)
    return payload


def agent_cases(agent_id: str) -> dict[str, Any]:
    """에이전트 하나의 판정 케이스다."""
    payload: dict[str, Any] = read_agent_cases(agent_id)
    return payload


def shared_contract(file_name: str) -> dict[str, Any]:
    """에이전트들이 함께 쓰는 계약 파일 하나를 읽는다."""
    payload: dict[str, Any] = read_json(f"agent/shared/{file_name}")
    return payload


def wire_contract(file_name: str) -> dict[str, Any]:
    """경계를 넘는 값의 계약 파일 하나를 읽는다."""
    payload: dict[str, Any] = read_json(f"wire/{file_name}")
    return payload


def workflow_contract(file_name: str) -> dict[str, Any]:
    """두 구현체의 작업 큐와 워크플로를 적은 계약 파일 하나를 읽는다."""
    payload: dict[str, Any] = yaml.safe_load(
        (contract_root() / "workflow" / file_name).read_text(encoding="utf-8")
    )
    return payload


def conformance_case(name: str) -> dict[str, Any]:
    """적합성 케이스 하나를 읽는다."""
    payload: dict[str, Any] = read_case(name)
    return payload
