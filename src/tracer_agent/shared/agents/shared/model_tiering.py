"""종류마다 고를 수 있는 모델을 소유하며 봉투와 설정이 함께 읽는다."""

from __future__ import annotations

from .execution_limits import contract_execution_limits
from .job_kinds import AgentJobKind

CHAT_KIND = "chat"

# 허용 목록과 예산은 한 쌍이라 목록을 넓히면 같은 예산이 다른 모델을 실행하므로 계약이 함께 갖는다.
ALLOWED_MODELS: dict[str, tuple[str, ...]] = {
    CHAT_KIND: contract_execution_limits()[CHAT_KIND].allowed_models,
    **{kind.wire: contract_execution_limits()[kind.value].allowed_models for kind in AgentJobKind},
}


def allowed_models(kind: str) -> tuple[str, ...]:
    """그 종류가 허용한 모델이며 모르는 종류는 아무것도 허용하지 않는다."""
    return ALLOWED_MODELS.get(kind, ())


def offered_models(kinds: tuple[str, ...]) -> frozenset[str]:
    """그 종류들이 모두 함께 허용하는 모델이며 설정 하나가 고를 수 있는 값이다."""
    known = [kind for kind in kinds if kind in ALLOWED_MODELS]
    if not known:
        return frozenset()
    shared = set(ALLOWED_MODELS[known[0]])
    for kind in known[1:]:
        shared &= set(ALLOWED_MODELS[kind])
    return frozenset(shared)
