"""검증 꼬리가 네 에이전트에서 함께 쓰는 검증 노드와 종단 노드의 기계를 소유한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping

from .execution.trace import ExecutionTrace
from .node import GraphNode


class ValidationNode[StateT, UpdateT: Mapping[str, object]](GraphNode[StateT, UpdateT], ABC):
    """결정적 검증을 실행하고 걸린 사유를 실행 궤적에 남긴다."""

    def __init__(self, trace: ExecutionTrace) -> None:
        self._trace = trace

    async def run(self, state: StateT) -> UpdateT:
        update, errors = self.validate(state)
        if errors:
            self._trace.record_orchestration_event(
                "validation.failed", "; ".join(errors), node_name=self.name
            )
            self.record_failure(state)
        return update

    @abstractmethod
    def validate(self, state: StateT) -> tuple[UpdateT, list[str]]:
        """이 에이전트의 검증을 실행해 상태 갱신과 모델이 고쳐야 하는 사유를 낸다."""

    def record_failure(self, _state: StateT, /) -> None:
        """검증이 걸린 실행을 계측에 남기며 셀 것이 없는 에이전트는 아무 일도 하지 않는다."""
        return


class ResultNode[StateT, UpdateT: Mapping[str, object]](GraphNode[StateT, UpdateT]):
    """확정과 빈 결과 두 자리에 같은 기계를 세우고 결과를 만드는 규칙만 슬라이스에서 받는다."""

    def __init__(self, name: str, build: Callable[[StateT], UpdateT]) -> None:
        self.name = name
        self._build = build

    async def run(self, state: StateT) -> UpdateT:
        return self._build(state)
