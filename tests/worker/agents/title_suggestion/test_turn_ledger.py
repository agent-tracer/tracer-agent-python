"""maxTurns가 실행 총량이라는 계약을 title-suggestion이 지키는지 고정한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import TITLE_SUGGESTION_PROMPT
from tracer_agent.shared.agents.shared.graph_state import remaining_turns
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionRequest
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.durable_graph import PriorSpend
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.title_suggestion.agent import TITLE_SUGGESTION_JOB

MAX_TURNS = int(WIRE_LIMITS["maxTurns"])
_FRESH = PriorSpend(resumed=False, cost_usd=0.0, turns=0)
_DRAFT: dict[str, Any] = {"suggestions": [{"title": "토큰 누수 수정", "rationale": "근거"}]}

_CONTEXT = {
    "title": "Untitled",
    "status": "completed",
    "totalEventCount": 3,
    "totalTurnCount": 1,
    "truncated": False,
    "turns": [{"turnIndex": 0, "askedText": "토큰 누수를 고쳐줘"}],
}


def _request() -> TitleSuggestionRequest:
    return TitleSuggestionRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "userId": "user-1",
            "jobId": "job-1",
            "taskId": "task-1",
            "context": _CONTEXT,
        }
    )


def _composed(chat: FakeToolLoopChat) -> Any:
    """프로덕션과 같은 compose로 리페어 예약을 떼고 이 실행의 노드를 세운다."""
    return TITLE_SUGGESTION_JOB.compose(
        _request(),
        FakeTracerApi(),
        ExecutionTrace(),
        TITLE_SUGGESTION_PROMPT,
        ChatPair(chat, None),
        _FRESH,
    )


def test_조사와_리페어_몫의_합이_실행_총량을_넘지_않는다() -> None:
    # 호출마다 maxTurns를 다시 열면 조사와 리페어가 각각 총량을 쓰고 실행이 두 배로 열린다.
    plan = _composed(FakeToolLoopChat([]))

    assert plan.initial["max_turns"] < MAX_TURNS
    assert plan.initial["max_turns"] > 0


async def test_조사가_풀을_다_써도_리페어는_예약한_턴으로_돈다() -> None:
    # 조사 노드가 리페어 예약을 함께 들고 있으면 그 예약이 조사에서 한 번 더 열린다.
    plan = _composed(FakeToolLoopChat([_DRAFT, _DRAFT]))
    state: dict[str, Any] = dict(plan.initial)

    state = _merged(state, await plan.context.nodes["investigate"](state))
    exhausted = {**state, "pool_turns_used": state["max_turns"], "validation_errors": ["고쳐라"]}
    repaired = await plan.context.nodes["repair"](exhausted)

    assert remaining_turns(exhausted) == 0
    # 리페어는 예약 리스로 돌아 풀이 비어도 모델을 부르고 그 지출을 풀에 되적지 않는다.
    assert repaired["model_turns_used"] == 1
    assert repaired["pool_turns_used"] == 0


async def test_조사가_그은_턴만_팬아웃_잔량에서_빠진다() -> None:
    plan = _composed(FakeToolLoopChat([_DRAFT]))
    state: dict[str, Any] = dict(plan.initial)

    investigated = await plan.context.nodes["investigate"](state)

    assert investigated["pool_turns_used"] == investigated["model_turns_used"] == 1
    assert investigated["pool_cost_usd"] == pytest.approx(investigated["model_cost_usd"])


def _merged(state: dict[str, Any], update: Any) -> dict[str, Any]:
    return {**state, **update}
