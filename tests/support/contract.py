"""계약 저장소가 소유한 값을 대조에 쓰도록 계약의 로더를 감싼다."""

from __future__ import annotations

from typing import Any

import yaml
from contract.conformance.runner.contract import (
    contract_root,
    read_agent_spec,
    read_case,
    read_json,
    read_version,
)

PINNED_VERSION = "0.5.0"


def contract_version() -> str:
    """이 저장소가 향하는 계약의 판이며 고정한 값과 대조한다."""
    return read_version()


def agent_spec(agent_id: str) -> dict[str, Any]:
    """에이전트 하나의 명세이며 프롬프트 조각과 도구와 출력과 케이스를 담는다."""
    spec: dict[str, Any] = read_agent_spec(agent_id)
    return spec


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
