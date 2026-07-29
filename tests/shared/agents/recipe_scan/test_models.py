"""recipe-scan 요청 봉투와 상태 리듀서의 누적 규칙을 검증한다."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import ValidationError

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tracer_agent.shared.agents.recipe_scan.models import (
    DispatchPlan,
    ProbeReport,
    ProvenanceCatalog,
    RecipeDraft,
    RecipeScanRequest,
    RecipeScanState,
)

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-1"}


def test_도메인_입력과_콜백_창구를_요구한다() -> None:
    with pytest.raises(ValidationError):
        RecipeScanRequest.model_validate({"model": "m", "apiKey": "k"})


def test_런타임_정의를_요청으로_받지_않는다() -> None:
    with pytest.raises(ValidationError):
        RecipeScanRequest.model_validate(
            {
                "model": "m",
                "apiKey": "k",
                "modelRates": WIRE_MODEL_RATES,
                "limits": WIRE_LIMITS,
                "taskId": "t1",
                "systemPrompt": "s",
                "outputSchema": {"type": "object"},
                "tools": [],
                "userId": "user-1",
                "completionCallback": _COMPLETION,
            }
        )


def test_도메인_봉투를_보존한다() -> None:
    req = RecipeScanRequest.model_validate(
        {
            "model": "m",
            "apiKey": "k",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "taskId": " t1 ",
            "language": "ko",
            "userPrompt": " 작업에서 레시피를 찾아줘 ",
            "userId": "user-1",
            "completionCallback": _COMPLETION,
        }
    )

    assert req.taskId == "t1"
    assert req.language == "ko"
    assert req.userPrompt == "작업에서 레시피를 찾아줘"
    assert req.deadlineMs == 720_000


def test_조사할_것이_없다는_빈_계획을_허용한다() -> None:
    assert DispatchPlan.model_validate({}).probes == []
    assert DispatchPlan.model_validate({"probes": []}).probes == []


PROBES = ("timeline", "rules", "repetition")


async def _probe(payload: dict[str, str]) -> dict[str, Any]:
    name = payload["probe"]
    return {
        "reports": [ProbeReport(probe=name, verdict="조사했다")],  # type: ignore[typeddict-item]
        "provenance": ProvenanceCatalog(eventIdsByTask={"task-1": {f"event-{name}"}}),
        "model_cost_usd": 0.5,
    }


def _fan(_state: RecipeScanState) -> list[Send]:
    return [Send("probe", {"probe": name}) for name in PROBES]


async def test_전문가가_병렬로_올린_보고가_모두_남는다() -> None:
    graph = StateGraph(RecipeScanState)
    graph.add_node("dispatch", lambda _state: {})
    graph.add_node("probe", _probe)
    graph.add_edge(START, "dispatch")
    graph.add_conditional_edges("dispatch", _fan, ["probe"])
    graph.add_edge("probe", END)

    final = await graph.compile().ainvoke(
        {"reports": [], "provenance": ProvenanceCatalog(), "model_cost_usd": 0.0}
    )

    # 리듀서가 없으면 동시 갱신이 서로를 덮어써 실행이 깨진다.
    assert sorted(report.probe for report in final["reports"]) == sorted(PROBES)
    assert final["provenance"].eventIdsByTask["task-1"] == {f"event-{name}" for name in PROBES}
    assert final["model_cost_usd"] == 1.5


def test_모델이_받는_스키마는_사람용_docstring을_싣지_않는다() -> None:
    for model in (DispatchPlan, ProbeReport, RecipeDraft):
        wire = json.dumps(convert_to_openai_tool(model), ensure_ascii=False)

        # docstring이 설명으로 새면 python만 영어 프롬프트 안에서 한글 스키마 설명을 받는다.
        assert not re.search(r"[가-힣]", wire), model.__name__
    assert DispatchPlan.__doc__ and ProbeReport.__doc__
