"""task-cleanup 요청 모델의 봉투 보존과 주입 거부를 검증한다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.support.contract import shared_contract
from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tracer_agent.shared.agents.task_cleanup.models import (
    CleanupDraft,
    TaskCleanupRequest,
    TriagePlan,
    slot_of,
)

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-1"}


def test_요청은_실행_봉투_밖의_정의를_거부한다() -> None:
    with pytest.raises(ValidationError):
        TaskCleanupRequest.model_validate(
            {
                "model": "claude-sonnet-4-6",
                "apiKey": "sk-test",
                "modelRates": WIRE_MODEL_RATES,
                "limits": WIRE_LIMITS,
                "scannedAt": "2026-07-14T00:00:00Z",
                "userId": "user-1",
                "batch": {"candidates": []},
                "completionCallback": _COMPLETION,
                "systemPrompt": "런타임이 정의를 밀어 넣는다",
            },
        )


def test_도메인_봉투와_한도를_보존한다() -> None:
    req = TaskCleanupRequest.model_validate(
        {
            "model": "m",
            "apiKey": "k",
            "modelRates": WIRE_MODEL_RATES,
            "limits": WIRE_LIMITS,
            "deadlineMs": 600_000,
            "scannedAt": "2026-07-13T00:00:00.000Z",
            "userId": "user-1",
            "batch": {"candidates": []},
            "language": "ko",
            "maxSuggestions": 20,
            "completionCallback": _COMPLETION,
        }
    )

    assert req.scannedAt == "2026-07-13T00:00:00.000Z"
    assert req.maxSuggestions == 20
    assert req.deadlineMs == 600_000


SLOT_FIELD = shared_contract("dispatch.plan.json")["uniqueness"]["keys"]["task-cleanup"]


def _assignment(slot: str, depth: str) -> dict[str, str]:
    """계약이 자리로 정한 필드에만 값을 실어 세운 배정 하나다."""
    return {SLOT_FIELD: slot, "depth": depth}


def test_한_계획이_같은_자리를_두_번_담지_않는다() -> None:
    # 나머지 칸이 달라도 계약이 자리로 정한 필드가 같으면 겹친 배정이다.
    twice = [_assignment("task-1", "deep"), _assignment("task-1", "shallow")]

    with pytest.raises(ValidationError):
        TriagePlan.model_validate({"inspect": twice})
    with pytest.raises(ValidationError):
        CleanupDraft.model_validate({"redispatch": twice})


def test_자리가_서로_다른_계획은_그대로_선다() -> None:
    plan = TriagePlan.model_validate(
        {"inspect": [_assignment("task-1", "deep"), _assignment("task-2", "shallow")]}
    )

    assert [slot_of(assignment) for assignment in plan.assignments] == ["task-1", "task-2"]
