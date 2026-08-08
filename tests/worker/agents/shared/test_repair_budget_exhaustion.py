"""수리 턴이 예약 예산에서 끊길 때 두 슬라이스가 같은 결말을 내는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import RECIPE_SCAN_PROMPT, TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft, RecipeScanRequest
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest
from tracer_agent.worker.agents.recipe_scan.agent import RECIPE_SCAN_JOB
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.durable_graph import PriorSpend
from tracer_agent.worker.agents.runtime.errors import BudgetExceeded
from tracer_agent.worker.agents.runtime.execution.trace import ExecutionTrace
from tracer_agent.worker.agents.runtime.llm.client import ChatPair
from tracer_agent.worker.agents.task_cleanup.agent import TASK_CLEANUP_JOB

_FRESH = PriorSpend(resumed=False, cost_usd=0.0, turns=0)
_ENVELOPE: dict[str, Any] = {
    "model": "claude-sonnet-4-6",
    "apiKey": "sk-test",
    "modelRates": WIRE_MODEL_RATES,
    "limits": WIRE_LIMITS,
    "userId": "user-1",
}

_SUGGESTION: dict[str, Any] = {
    "kind": "archive",
    "taskId": "task-1",
    "rationale": "의미 있는 활동이 없다",
    "evidenceEventIds": [],
}
_RECIPE: dict[str, Any] = {
    "title": "Add migration",
    "intent": "마이그레이션",
    "description": "설명",
    "summary_md": "- a",
    "request": "사용자가 마이그레이션 작업을 recipe로 만들라고 했다.",
    "corrections": [],
    "pitfalls": [],
    "governing_rules": [],
    "contributing_slices": [{"taskId": "task-1", "turnIds": ["turn-1"], "eventIds": ["event-1"]}],
    "rationale": "근거",
}


class BudgetBlownChat(FakeToolLoopChat):
    """첫 호출에서 실행 예산 초과로 끊기는 모델 대역이다."""

    def __init__(self) -> None:
        super().__init__([])

    async def ainvoke(self, _messages: list[object]) -> object:
        raise BudgetExceeded("repair exceeded execution model budget")


async def test_수리가_예산에서_끊겨도_정리_잡은_통과한_제안을_안고_이어간다() -> None:
    plan = TASK_CLEANUP_JOB.compose(
        TaskCleanupRequest.model_validate(
            {
                **_ENVELOPE,
                "scannedAt": "2026-07-14T00:00:00Z",
                "maxSuggestions": 5,
                "language": "ko",
                "batch": {"candidates": []},
            }
        ),
        FakeTracerApi(),
        ExecutionTrace(),
        TASK_CLEANUP_PROMPT,
        ChatPair(BudgetBlownChat(), None),
        _FRESH,
    )
    state: dict[str, Any] = {
        **dict(plan.initial),
        "suggestions": [_SUGGESTION],
        "validation_errors": ["고쳐라"],
    }

    repaired = await plan.context.nodes["repair"](state)

    assert repaired["suggestions"] == [_SUGGESTION]
    assert repaired["repair_attempted"] is True
    # 예약 리스로 돈 호출이므로 끊긴 수리도 팬아웃 풀을 줄이지 않는다.
    assert repaired["pool_turns_used"] == 0


async def test_수리가_예산에서_끊기면_스캔_잡은_후보를_비우고_생성_실패로_적는다() -> None:
    plan = RECIPE_SCAN_JOB.compose(
        RecipeScanRequest.model_validate({**_ENVELOPE, "taskId": "task-1", "language": "ko"}),
        FakeTracerApi(),
        ExecutionTrace(),
        RECIPE_SCAN_PROMPT,
        ChatPair(BudgetBlownChat(), None),
        _FRESH,
    )
    state: dict[str, Any] = {
        **dict(plan.initial),
        "candidates": list(RecipeDraft.model_validate({"recipes": [_RECIPE]}).recipes),
        "validation_errors": ["고쳐라"],
    }

    repaired = await plan.context.nodes["repair"](state)

    assert repaired["candidates"] == []
    assert repaired["repair_attempted"] is True
    assert repaired["empty_result_reason"] == "generation-degraded"
