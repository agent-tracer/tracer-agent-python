"""빈 결과로 끝난 실행이 남기는 사유의 어휘를 계약에서 읽는다."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, get_args

from tracer_agent.shared.agents.shared.models import EmptyResultReason

from .contract_prompt_source import CONTRACT_ROOT, ContractPromptUnavailable

_EXECUTION_BUDGET_PATH = CONTRACT_ROOT / "agent" / "shared" / "execution.budget.json"
# 계획·조사·검증·전달 가운데 한 단계가 실패해 빈 결과로 끝난 실행이 남기는 사유다.
DEGRADED: EmptyResultReason = "generation-degraded"
# 조사는 실행했으나 근거가 모자라 후보를 세울 수 없던 실행이 남기는 사유다.
INSUFFICIENT_EVIDENCE: EmptyResultReason = "insufficient-evidence"


@lru_cache(maxsize=1)
def _declared() -> dict[str, Any]:
    """계약이 어휘를 소유하므로 코드의 어휘가 계약과 갈리면 그 자리에서 멈춘다."""
    document: dict[str, Any] = json.loads(_EXECUTION_BUDGET_PATH.read_text(encoding="utf-8"))
    declared: dict[str, Any] = document["orchestratorFailureDemotion"]["emptyResultReason"]
    values = tuple(declared["values"])
    if values != get_args(EmptyResultReason):
        raise ContractPromptUnavailable(
            f"execution.budget.json: emptyResultReason values must be {get_args(EmptyResultReason)}, "
            f"got {values}"
        )
    return declared


def empty_result_reasons() -> tuple[EmptyResultReason, ...]:
    """빈 결과 사유로 쓸 수 있는 값 전부이며 계약이 적은 순서를 지킨다."""
    return get_args(EmptyResultReason)


def default_empty_result_reason() -> EmptyResultReason:
    """실행이 실패한 자리를 찾지 못했을 때 남기는 사유다."""
    value: EmptyResultReason = _declared()["default"]
    if value not in empty_result_reasons():
        raise ContractPromptUnavailable(f"execution.budget.json: unknown emptyResultReason default: {value}")
    return value
