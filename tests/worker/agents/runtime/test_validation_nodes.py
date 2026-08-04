"""검증 꼬리가 함께 쓰는 검증 노드와 종단 노드의 기계를 검증한다(모델 호출 없음)."""

from __future__ import annotations

from typing import Any, TypedDict

from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.node import NodeRegistry
from tracer_agent.worker.agents.runtime.routes import EMPTY, FINALIZE
from tracer_agent.worker.agents.runtime.validation_nodes import ResultNode, ValidationNode


class _Update(TypedDict):
    validation_errors: list[str]


class _Validate(ValidationNode[dict[str, Any], _Update]):
    name = "validate_candidate"

    def __init__(self, trace: ExecutionTrace, errors: list[str]) -> None:
        super().__init__(trace)
        self._errors = errors
        self.recorded = 0

    def validate(self, _state: dict[str, Any]) -> tuple[_Update, list[str]]:
        return {"validation_errors": self._errors}, self._errors

    def record_failure(self, _state: dict[str, Any], /) -> None:
        self.recorded += 1


class _Silent(_Validate):
    """계측을 재정의하지 않는 에이전트는 기본 구현이 아무 일도 하지 않아야 한다."""

    def record_failure(self, _state: dict[str, Any], /) -> None:
        return


def _events(trace: ExecutionTrace) -> list[tuple[str | None, str | None, str]]:
    return [(step.eventKind, step.nodeName, step.content) for step in trace.steps]


async def test_검증이_걸리면_사유를_한_줄로_모아_궤적에_남긴다() -> None:
    trace = ExecutionTrace()
    node = _Validate(trace, ["첫 사유", "둘째 사유"])

    update = await node.run({})

    assert update == {"validation_errors": ["첫 사유", "둘째 사유"]}
    assert _events(trace) == [("validation.failed", "validate_candidate", "첫 사유; 둘째 사유")]
    assert node.recorded == 1


async def test_검증을_통과하면_궤적에도_계측에도_남기지_않는다() -> None:
    trace = ExecutionTrace()
    node = _Validate(trace, [])

    update = await node.run({})

    assert update == {"validation_errors": []}
    assert trace.steps == []
    assert node.recorded == 0


async def test_계측을_재정의하지_않아도_검증은_그대로_돈다() -> None:
    trace = ExecutionTrace()
    node = _Silent(trace, ["사유"])

    update = await node.run({})

    assert update == {"validation_errors": ["사유"]}
    assert _events(trace) == [("validation.failed", "validate_candidate", "사유")]


async def test_종단_노드는_자기_자리의_이름을_인스턴스가_갖는다() -> None:
    # 같은 기계를 확정과 빈 결과 두 자리에 세우므로 이름은 클래스가 아니라 인스턴스가 정한다.
    finalize: ResultNode[dict[str, Any], _Update] = ResultNode(
        FINALIZE, lambda state: {"validation_errors": list(state["errors"])}
    )
    empty: ResultNode[dict[str, Any], _Update] = ResultNode(EMPTY, lambda _state: {"validation_errors": []})

    assert finalize.name == FINALIZE
    assert empty.name == EMPTY
    assert await finalize.run({"errors": ["사유"]}) == {"validation_errors": ["사유"]}
    assert await empty.run({"errors": ["사유"]}) == {"validation_errors": []}


async def test_노드_사전은_인스턴스가_정한_이름도_그대로_대조한다() -> None:
    # 이름을 인스턴스가 가지면서도 등록한 열쇠와 어긋나면 조립 시점에 걸려야 한다.
    registry = NodeRegistry(
        {
            FINALIZE: ResultNode(FINALIZE, lambda _state: {"validation_errors": []}),
            EMPTY: ResultNode(EMPTY, lambda _state: {"validation_errors": []}),
        },
        frozenset({FINALIZE, EMPTY}),
    )

    assert await registry[FINALIZE]({}) == {"validation_errors": []}
