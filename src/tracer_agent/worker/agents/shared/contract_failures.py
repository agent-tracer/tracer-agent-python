"""도구와 조사가 무너졌을 때 모델이 읽는 문구를 계약에서 읽는다."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from .contract_prompt_source import CONTRACT_ROOT, ContractPromptUnavailable

# 도구 하나가 무너졌을 때 모델이 읽는 문구의 계약 자리다.
TOOL_FAILED_KEY = "toolFailed"
# 조사 하나가 통째로 무너졌을 때 조율자가 읽는 문구의 계약 자리다.
WORKER_FAILED_KEY = "workerFailed"


@lru_cache(maxsize=8)
def _failures(agent_name: str) -> Mapping[str, str]:
    path = CONTRACT_ROOT / "agent" / agent_name / "tool.json"
    try:
        contract: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except OSError as unreadable:
        raise ContractPromptUnavailable(f"contract file is unreadable: {path}") from unreadable
    except ValueError as invalid:
        raise ContractPromptUnavailable(f"contract file is not valid JSON: {path}") from invalid
    declared = contract.get("failures")
    if not isinstance(declared, Mapping):
        raise ContractPromptUnavailable(f"contract declares no failure texts: {path}")
    return {str(key): str(value) for key, value in declared.items()}


def failure_text(agent_name: str, key: str) -> str:
    """두 구현체가 같은 실패에 같은 글자를 내도록 계약이 소유한 문구를 낸다."""
    texts = _failures(agent_name)
    if key not in texts:
        raise ContractPromptUnavailable(f"{agent_name} contract declares no failure text: {key}")
    return texts[key]
