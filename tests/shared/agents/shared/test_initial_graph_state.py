"""네 에이전트의 초기 그래프 상태가 자기 상태 채널을 빠짐없이 채우는지 검증한다."""

from __future__ import annotations

from typing import Any

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tracer_agent.shared.agents.chat.models import (
    ChatRequest,
    ChatState,
    initial_chat_state,
)
from tracer_agent.shared.agents.recipe_scan.models import (
    RecipeScanRequest,
    RecipeScanState,
    initial_recipe_scan_state,
)
from tracer_agent.shared.agents.task_cleanup.models import (
    TaskCleanupRequest,
    TaskCleanupState,
    initial_task_cleanup_state,
)
from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestionRequest,
    TitleSuggestionState,
    initial_title_suggestion_state,
)

_ENVELOPE: dict[str, Any] = {
    "model": "claude-haiku-4-5",
    "apiKey": "sk-test",
    "modelRates": WIRE_MODEL_RATES,
    "limits": WIRE_LIMITS,
    "userId": "user-1",
}

_TITLE_CONTEXT: dict[str, Any] = {
    "title": "Untitled",
    "status": "completed",
    "totalEventCount": 3,
    "totalTurnCount": 1,
    "truncated": False,
    "turns": [{"turnIndex": 0, "askedText": "토큰 누수를 고쳐줘"}],
}


def _chat_state() -> ChatState:
    req = ChatRequest.model_validate(
        {
            **_ENVELOPE,
            "threadId": "thread-1",
            "executionId": "execution-1",
            "language": "ko",
            "messages": [{"role": "user", "content": "task-1 아카이브해줘"}],
        }
    )
    return initial_chat_state(req)


def _recipe_state() -> RecipeScanState:
    req = RecipeScanRequest.model_validate({**_ENVELOPE, "jobId": "job-1", "taskId": "task-1"})
    return initial_recipe_scan_state(req, max_cost_usd=1.5, max_turns=9)


def _cleanup_state() -> TaskCleanupState:
    req = TaskCleanupRequest.model_validate(
        {
            **_ENVELOPE,
            "jobId": "job-1",
            "scannedAt": "2026-07-13T00:00:00.000Z",
            "maxSuggestions": 20,
            "batch": {"candidates": []},
        }
    )
    return initial_task_cleanup_state(req, max_cost_usd=1.5, max_turns=9)


def _title_state() -> TitleSuggestionState:
    req = TitleSuggestionRequest.model_validate(
        {**_ENVELOPE, "jobId": "job-1", "taskId": "task-1", "context": _TITLE_CONTEXT}
    )
    return initial_title_suggestion_state(req, max_cost_usd=1.5, max_turns=9)


_STATES: tuple[tuple[str, dict[str, Any], type[Any]], ...] = (
    ("chat", dict(_chat_state()), ChatState),
    ("recipe-scan", dict(_recipe_state()), RecipeScanState),
    ("task-cleanup", dict(_cleanup_state()), TaskCleanupState),
    ("title-suggestion", dict(_title_state()), TitleSuggestionState),
)


def test_초기_상태가_선언된_채널을_빠짐없이_채운다() -> None:
    # 채널을 늘리고 초기값을 빠뜨리면 그 칸은 첫 노드가 읽을 때에야 없는 것이 드러난다.
    for name, state, schema in _STATES:
        declared = set(schema.__required_keys__) | set(schema.__optional_keys__)
        assert set(state) == declared, name


def test_초기_상태의_예산_스냅숏은_영에서_시작한다() -> None:
    # 재개가 앞선 시도의 지출을 더하므로 처음 시작하는 상태는 두 칸이 비어 있어야 한다.
    for name, state, _schema in _STATES:
        assert state["model_cost_usd"] == 0.0, name
        assert state["model_turns_used"] == 0, name


def test_초기_상태는_수리를_아직_쓰지_않은_것으로_시작한다() -> None:
    for name, state, _schema in _STATES:
        if "repair_attempted" in state:
            assert state["repair_attempted"] is False, name


def test_잔량은_예약을_뗀_뒤의_값을_그대로_받는다() -> None:
    # 요청의 한도를 그대로 실으면 예약분을 팬아웃과 조사가 다시 나눠 쓴다.
    for name, state in (
        ("recipe-scan", dict(_recipe_state())),
        ("task-cleanup", dict(_cleanup_state())),
        ("title-suggestion", dict(_title_state())),
    ):
        assert state["max_cost_usd"] == 1.5, name
        assert state["max_turns"] == 9, name
        assert WIRE_LIMITS["budgetUsd"] != state["max_cost_usd"], name
        assert WIRE_LIMITS["maxTurns"] != state["max_turns"], name
