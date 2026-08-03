"""검증 뒤의 그래프가 밟을 수 있는 경로의 이름과 그 판정이 읽는 상태를 소유한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypedDict

type ValidationRouteName = Literal["repair", "finalize", "empty"]
type ValidationRoute[StateT] = Callable[[StateT], ValidationRouteName]

REPAIR: ValidationRouteName = "repair"
FINALIZE: ValidationRouteName = "finalize"
EMPTY: ValidationRouteName = "empty"


class ValidatedState(TypedDict):
    """검증 꼬리가 경로를 가를 때 읽는 상태의 최소 모양이다."""

    validation_errors: list[str]
    repair_attempted: bool
