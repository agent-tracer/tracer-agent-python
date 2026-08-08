"""maxTurns가 실행 총량이라는 계약을 task-cleanup이 지키는지 고정한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import CONTRACT_VERSION, TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.shared.graph_state import remaining_cost_usd, remaining_turns
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest, TriagePlan
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.durable_graph import PriorSpend
from tracer_agent.worker.agents.runtime.execution.runner import ExecutionRequest, execute
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.task_cleanup.agent import TASK_CLEANUP_JOB
from tracer_agent.worker.agents.task_cleanup.graph import _dispatch

MAX_TURNS = int(WIRE_LIMITS["maxTurns"])
_FRESH = PriorSpend(resumed=False, cost_usd=0.0, turns=0)
_CANDIDATE: dict[str, Any] = {
    "id": "task-1",
    "visibleTitle": "제목",
    "status": "completed",
    "lastEventAt": None,
    "hasEvents": False,
    "activeChildCount": 0,
    "candidateReasons": ["stale"],
}
_DRAFT: dict[str, Any] = {
    "suggestions": [
        {
            "kind": "archive",
            "taskId": "task-1",
            "rationale": "의미 있는 활동이 없다",
            "evidenceEventIds": [],
        }
    ]
}


def _request(*, candidates: list[dict[str, Any]] | None = None) -> TaskCleanupRequest:
    return TaskCleanupRequest.model_validate(
        {
            "model": "claude-sonnet-4-6",
            "apiKey": "sk-test",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "scannedAt": "2026-07-14T00:00:00Z",
            "userId": "user-1",
            "maxSuggestions": 5,
            "language": "ko",
            "batch": {"candidates": candidates if candidates is not None else []},
        }
    )


def _composed(chat: FakeToolLoopChat, req: TaskCleanupRequest) -> Any:
    """프로덕션과 같은 compose로 계약이 정한 예약을 떼고 이 실행의 노드를 세운다."""
    return TASK_CLEANUP_JOB.compose(
        req, FakeTracerApi(), ExecutionTrace(), TASK_CLEANUP_PROMPT, ChatPair(chat, None), _FRESH
    )


async def test_선별이_적은_지출을_안고도_팬아웃이_예약을_뺀_잔량_전부를_연다() -> None:
    # 예약분을 풀에서 다시 빼면 계약이 검토자에게 주기로 한 턴이 그만큼 줄어든다.
    req = _request(candidates=[_CANDIDATE])
    plan = _composed(FakeToolLoopChat([]), req)
    state: dict[str, Any] = dict(plan.initial)
    pool_turns = state["max_turns"]

    state = {**state, **await plan.context.nodes["triage"](state)}
    triaged = TriagePlan(
        inspect=[{"taskId": f"task-{index}", "depth": "deep"} for index in range(3)]  # type: ignore[list-item]
    )
    sends = _dispatch({**state, "plan": triaged})

    assert state["model_turns_used"] == 1
    assert remaining_turns(state) == pool_turns
    assert remaining_cost_usd(state) == pytest.approx(state["max_cost_usd"])
    assert sum(send.arg.max_turns for send in sends) == pool_turns


async def test_예약과_팬아웃_잔량의_합이_실행_총량을_넘지_않는다() -> None:
    plan = _composed(FakeToolLoopChat([]), _request())
    triaged = TriagePlan(
        inspect=[{"taskId": f"task-{index}", "depth": "deep"} for index in range(10)]  # type: ignore[list-item]
    )

    sends = _dispatch({**dict(plan.initial), "plan": triaged})

    # 예약 셋과 팬아웃이 함께 실행 총량 안에 들어야 노드마다 상한이 다시 열리지 않는다.
    assert sum(send.arg.max_turns for send in sends) <= plan.initial["max_turns"]
    assert plan.initial["max_turns"] < MAX_TURNS


async def test_실행_하나가_부른_모델_호출이_실행_총량을_넘지_않는다() -> None:
    chat = FakeToolLoopChat([_DRAFT], plan={"inspect": [{"taskId": "task-1", "depth": "deep"}]})
    req = _request(candidates=[_CANDIDATE])

    res = await execute(
        ExecutionRequest(
            label="task-cleanup",
            model=req.model,
            deadline_ms=req.deadlineMs,
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        ),
        lambda usage: TASK_CLEANUP_JOB.run(
            req, FakeTracerApi(), usage, TASK_CLEANUP_PROMPT, None, ChatPair(chat, None)
        ),
    )

    assert res.error is None
    # 대역은 호출마다 요청을 하나씩 적으므로 이 수가 곧 이 실행이 그은 모델 턴이다.
    assert len(chat.requests) <= MAX_TURNS
