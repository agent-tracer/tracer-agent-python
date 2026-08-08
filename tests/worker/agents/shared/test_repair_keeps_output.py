"""수리 턴이 추가 조사를 요청해도 이미 통과한 산출을 잃지 않는지 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeToolLoopChat
from tests.support.prompts import RECIPE_SCAN_PROMPT, TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import RecipeDraft, RecipeScanRequest
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest
from tracer_agent.worker.agents.recipe_scan.agent import RECIPE_SCAN_JOB
from tracer_agent.worker.agents.runtime.__fakes__.tracer_api import FakeTracerApi
from tracer_agent.worker.agents.runtime.durable_graph import PriorSpend
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


async def test_수리가_추가_조사를_요청하면_통과한_제안을_그대로_남긴다() -> None:
    # 수리 초안에는 redispatch를 실을 자리가 없어 그 요청을 빈 제안으로 읽으면 근거가 멀쩡한 제안이 사라진다.
    chat = FakeToolLoopChat([{"suggestions": [], "redispatch": [{"taskId": "task-2", "depth": "deep"}]}])
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
        ChatPair(chat, None),
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


async def test_수리가_추가_파견을_요청하면_통과한_후보를_그대로_남긴다() -> None:
    chat = FakeToolLoopChat(
        [{"recipes": [], "redispatch": [{"probe": "rules", "depth": "deep", "question": "무엇"}]}]
    )
    plan = RECIPE_SCAN_JOB.compose(
        RecipeScanRequest.model_validate({**_ENVELOPE, "taskId": "task-1", "language": "ko"}),
        FakeTracerApi(),
        ExecutionTrace(),
        RECIPE_SCAN_PROMPT,
        ChatPair(chat, None),
        _FRESH,
    )
    candidates = list(RecipeDraft.model_validate({"recipes": [_RECIPE]}).recipes)
    state: dict[str, Any] = {
        **dict(plan.initial),
        "candidates": candidates,
        "validation_errors": ["고쳐라"],
    }

    repaired = await plan.context.nodes["repair"](state)

    assert repaired["candidates"] == candidates
    assert repaired["repair_attempted"] is True
