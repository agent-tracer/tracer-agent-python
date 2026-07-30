"""큐의 완전한 이름을 배포가 준 접두사와 계약의 키가 만드는지 검증한다."""

from __future__ import annotations

import pytest

from tests.support.contract import workflow_contract
from tracer_agent.shared.config import (
    DEFAULT_TASK_QUEUE_PREFIX,
    TASK_QUEUE_PREFIX_ENV,
    task_queue,
)

_NAMING = workflow_contract("queues.yaml")["naming"]
_QUEUE_KEYS = workflow_contract("queues.yaml")["queues"]


def test_접두사를_주는_환경변수와_기본값이_계약이_적은_것과_같다() -> None:
    assert _NAMING["prefixEnv"] == TASK_QUEUE_PREFIX_ENV
    assert _NAMING["defaultPrefix"] == DEFAULT_TASK_QUEUE_PREFIX
    assert _NAMING["pattern"] == "{prefix}-{key}"


def test_배포가_접두사를_주지_않으면_기본값이_이름을_만든다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TASK_QUEUE_PREFIX_ENV, raising=False)

    assert {key: task_queue(key) for key in _QUEUE_KEYS} == {
        "chat": "agent-chat",
        "jobs": "agent-jobs",
        "generate": "agent-generate",
    }


def test_배포가_준_접두사가_계약의_키_셋_전부에_붙는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TASK_QUEUE_PREFIX_ENV, "side-by-side")

    assert {task_queue(key) for key in _QUEUE_KEYS} == {
        "side-by-side-chat",
        "side-by-side-jobs",
        "side-by-side-generate",
    }


def test_빈_접두사는_기본값으로_물러선다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TASK_QUEUE_PREFIX_ENV, "   ")

    assert task_queue("chat") == "agent-chat"


def test_계약이_큐_키_셋을_적는다() -> None:
    assert set(_QUEUE_KEYS) == {"chat", "jobs", "generate"}
