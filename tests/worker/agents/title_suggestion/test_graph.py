"""title-suggestion 도구 루프와 결정적 검증을 검증한다(페이크 모델, 네트워크 없음)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tests.support.fakes import WIRE_LIMITS, WIRE_MODEL_RATES, FakeLedger, FakeToolLoopChat
from tests.support.narrate import narrate
from tracer_agent.shared.agents.shared.models import AgentResponse
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionRequest
from tracer_agent.worker.agents.runtime.execution.runner import execute
from tracer_agent.worker.agents.title_suggestion import agent as title_mod

_COMPLETION = {"url": "http://worker:8810/runs/complete", "token": "done-title"}
_CONTEXT = {
    "title": "Untitled",
    "status": "completed",
    "workspacePath": "/workspace/project",
    "totalEventCount": 300,
    "totalTurnCount": 25,
    "truncated": True,
    "turns": [
        {
            "turnIndex": 1,
            "askedText": "인증 미들웨어의 토큰 누수를 고쳐줘",
            "assistantText": "회귀 테스트를 추가하고 누수를 수정했습니다.",
        }
    ],
}

_SUGGESTIONS = {
    "suggestions": [
        {"title": "인증 토큰 누수 수정", "rationale": "누수 수정이 핵심 작업이다."},
        {"title": "인증 회귀 테스트 추가", "rationale": "회귀 검증을 함께 추가했다."},
    ]
}

_EVENT_ROWS = [
    {
        "id": "event-1",
        "seq": 41,
        "kind": "agent_tracer.user.message",
        "title": "토큰 누수 수정",
        "body": None,
        "tool_name": None,
        "file_paths": ["src/auth.ts"],
        "occurred_at": datetime(2026, 7, 19, 3, 0, tzinfo=UTC),
    }
]


def _request(**overrides: Any) -> TitleSuggestionRequest:
    values: dict[str, Any] = {
        "model": "claude-haiku-4-5",
        "apiKey": "sk-test",
        "modelRates": WIRE_MODEL_RATES,
        "limits": WIRE_LIMITS,
        "jobId": "job-1",
        "taskId": "task-1",
        "language": "ko",
        "context": _CONTEXT,
        "userId": "user-1",
        "completionCallback": _COMPLETION,
    }
    values.update(overrides)
    return TitleSuggestionRequest.model_validate(values)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    turns: list[Any],
    ledger: FakeLedger | None = None,
    **request_overrides: Any,
) -> tuple[FakeToolLoopChat, AgentResponse, FakeLedger]:
    chat = FakeToolLoopChat(turns)
    monkeypatch.setattr(title_mod, "make_chat", lambda *_args, **_kwargs: chat)
    req = _request(**request_overrides)
    fake_ledger = ledger or FakeLedger()
    result = await execute(
        "title-suggestion",
        req.model,
        req.deadlineMs,
        lambda usage: title_mod.run_title_suggestion(req, fake_ledger, usage),
    )
    return chat, result, fake_ledger


async def test_대화_발췌로_충분하면_도구를_부르지_않고_제목을_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _chat, res, ledger = await _run(monkeypatch, [_SUGGESTIONS])

    assert res.error is None
    assert ledger.queries == []
    assert [item["title"] for item in res.data["suggestions"]] == [
        "인증 토큰 누수 수정",
        "인증 회귀 테스트 추가",
    ]
    narrate("title-suggestion :: 대화 발췌만으로 도구 없이 제목을 낸다", res)


async def test_현재_제목이_적절하면_빈_결과를_낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    _chat, res, _ledger = await _run(monkeypatch, [{"suggestions": []}])

    assert res.error is None and res.data == {"suggestions": []}
    narrate("title-suggestion :: 현재 제목이 이미 적절하면 빈 제안을 낸다", res)


async def test_발췌가_부족하면_모델이_스스로_이벤트를_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = FakeLedger(_EVENT_ROWS)
    turns: list[Any] = [
        [{"name": "get_task_events", "args": {"taskId": "task-1"}}],
        _SUGGESTIONS,
    ]

    _chat, res, fake_ledger = await _run(monkeypatch, turns, ledger)

    assert res.error is None
    # 조회는 태스크와 사용자 범위로 좁혀지고 상한보다 한 행 더 읽어 truncated를 판단한다.
    assert fake_ledger.queries == [{"desc": False, "args": ["task-1", "user-1", None, 101]}]
    assert [step.toolName for step in res.steps if step.role == "tool"] == ["get_task_events"]
    narrate("title-suggestion :: 발췌가 부족하면 태스크 이벤트를 직접 읽는다", res)


async def test_현재_제목을_되풀이한_후보는_한_번_수정한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = {
        "suggestions": [
            {"title": "Untitled", "rationale": "현재 제목을 그대로 되풀이한다."},
            {"title": "인증 회귀 테스트 추가", "rationale": "회귀 검증을 추가했다."},
        ]
    }

    _chat, res, _ledger = await _run(monkeypatch, [repeated, _SUGGESTIONS])

    assert res.error is None
    assert [item["title"] for item in res.data["suggestions"]] == [
        "인증 토큰 누수 수정",
        "인증 회귀 테스트 추가",
    ]
    failures = [step for step in res.steps if step.eventKind == "validation.failed"]
    assert len(failures) == 1 and "repeats the current title" in failures[0].content
    narrate("title-suggestion :: 현재 제목을 되풀이한 후보는 한 번 수정한다", res)


async def test_수정_후에도_후보가_유효하지_않으면_빈_결과를_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = {"suggestions": [{"title": "Untitled", "rationale": "여전히 현재 제목이다."}]}

    _chat, res, _ledger = await _run(monkeypatch, [invalid, invalid])

    assert res.error is None and res.data == {"suggestions": []}
    assert sum(step.eventKind == "validation.failed" for step in res.steps) == 2
    narrate("title-suggestion :: 수정 후에도 후보가 유효하지 않으면 빈 결과를 낸다", res)


async def test_단가를_모르는_모델은_내부_예산을_우회하지_못한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _chat, res, _ledger = await _run(monkeypatch, [_SUGGESTIONS], model="claude-custom-alias")

    assert res.error is not None
    assert "cannot enforce its internal budget" in res.error.summary
    narrate("title-suggestion :: 단가를 모르는 모델은 내부 예산을 우회하지 못한다", res)
