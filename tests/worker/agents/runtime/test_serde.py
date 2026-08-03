"""체크포인트 직렬화기가 밝힌 타입만 되살리는지 검증한다(모델 호출 없음)."""

from __future__ import annotations

import importlib
import logging
import pkgutil
import warnings

import pytest
from pydantic import BaseModel

from tracer_agent.shared import agents
from tracer_agent.shared.agents.recipe_scan.models import (
    ProbeAssignment,
    ProbeDispatch,
    ProvenanceCatalog,
)
from tracer_agent.worker.agents.runtime.serde import (
    _CHECKPOINTED_ROOTS,
    checkpointed_models,
    graph_serde,
)


class _낯선모델(BaseModel):
    value: str


def _declared_states() -> set[type]:
    """에이전트 모델 모듈이 선언한 그래프 상태 타입을 모두 찾는다."""
    found: set[type] = set()
    for module in pkgutil.walk_packages(agents.__path__, f"{agents.__name__}."):
        if not module.name.endswith(".models"):
            continue
        loaded = importlib.import_module(module.name)
        for name, value in vars(loaded).items():
            if name.endswith("State") and isinstance(value, type) and value.__module__ == module.name:
                found.add(value)
    return found


class Test체크포인트직렬화기:
    def test_선언된_상태를_빠짐없이_뿌리로_둔다(self) -> None:
        # 새 상태가 뿌리 목록에서 빠지면 그 상태가 담는 모델이 되살아나지 않는다.
        assert _declared_states() <= set(_CHECKPOINTED_ROOTS)

    def test_상태가_담는_모델을_모두_밝힌다(self) -> None:
        names = {model.__name__ for model in checkpointed_models()}

        assert {"ProvenanceCatalog", "ProbeDispatch", "TriagePlan", "TitleSuggestionDraft"} <= names

    def test_밝힌_모델을_경고_없이_되살린다(self) -> None:
        serde = graph_serde()
        catalog = ProvenanceCatalog(eventIdsByTask={"task-1": {"event-1"}}, ruleIds={"rule-1"})

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            restored = serde.loads_typed(serde.dumps_typed(catalog))

        assert restored == catalog

    def test_팬아웃이_실어_보내는_짐을_되살린다(self) -> None:
        serde = graph_serde()
        dispatch = ProbeDispatch(
            assignment=ProbeAssignment(probe="timeline", question="배포가 언제 무너졌는가", weight=1),
            siblings=[],
            max_turns=2,
            max_cost_usd=0.5,
        )

        restored = serde.loads_typed(serde.dumps_typed(dispatch))

        assert restored == dispatch

    def test_밝히지_않은_모델은_되살리지_않는다(self, caplog: pytest.LogCaptureFixture) -> None:
        serde = graph_serde()

        with caplog.at_level(logging.WARNING):
            restored = serde.loads_typed(serde.dumps_typed(_낯선모델(value="x")))

        assert not isinstance(restored, _낯선모델)
        assert "_낯선모델" in caplog.text
