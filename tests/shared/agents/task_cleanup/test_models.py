"""task-cleanup 요청 모델의 봉투 보존과 주입 거부를 검증한다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES
from tracer_agent.shared.agents.task_cleanup.models import TaskCleanupRequest

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
    assert req.deadlineMs == 300_000
