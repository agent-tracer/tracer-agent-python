"""task-cleanup의 역할별 프롬프트 조합을 콘솔에 펴 보이고 핵심 구성을 단언한다."""

from __future__ import annotations

from tests.support.prompts import TASK_CLEANUP_PROMPT
from tracer_agent.shared.agents.task_cleanup.models import (
    CandidateReason,
    CleanupBatch,
    CleanupCandidate,
    InspectReport,
)
from tracer_agent.worker.agents.task_cleanup.prompts import (
    build_inspect_prompt,
    build_prompt_bundle,
    build_triage_prompt,
    build_user_prompt,
)

_PROMPTS = build_prompt_bundle(TASK_CLEANUP_PROMPT)
INVESTIGATOR_SYSTEM_PROMPT = _PROMPTS.investigator_system
TRIAGE_SYSTEM_PROMPT = _PROMPTS.triage_system
INSPECT_SYSTEM_PROMPT = _PROMPTS.inspect_system
REPAIR_DIRECTIVE = _PROMPTS.repair_directive


def _flat(prompt: str) -> str:
    return " ".join(prompt.split())


def _show(role: str, system: str, user: str) -> None:
    print(f"\n───────── task-cleanup :: {role} ─────────")
    print("[system]")
    print(system)
    print("[user]")
    print(user)


def test_선별자는_후보_목록을_요청으로_받는다() -> None:
    batch = CleanupBatch(
        candidates=[
            CleanupCandidate(
                id="task-1",
                visibleTitle="정리해줘",
                status="running",
                lastEventAt=None,
                hasEvents=False,
                activeChildCount=0,
                candidateReasons=[CandidateReason.NO_EVENTS],
            )
        ]
    )

    user, listed = build_triage_prompt(TASK_CLEANUP_PROMPT, batch)

    _show("triage (선별자)", TRIAGE_SYSTEM_PROMPT, user)
    assert [candidate.id for candidate in listed] == ["task-1"]
    assert "Candidates in this batch: 1" in user
    assert "The qualified candidates in this batch:" in user
    assert "- task-1 | running | events: no | last: none | reasons: no-events | title: 정리해줘" in user


def test_선별자가_후보_목록의_필드와_신호를_안다() -> None:
    for fact in ("hasEvents", "lastEventAt", "candidateReasons"):
        assert fact in TRIAGE_SYSTEM_PROMPT
    for reason in ("no-events", "stale", "duplicate-title", "placeholder-title"):
        assert reason in TRIAGE_SYSTEM_PROMPT
    assert "assign every candidate that may be archivable" in _flat(TRIAGE_SYSTEM_PROMPT)
    assert "assigning nothing ends the scan with no suggestions" in _flat(TRIAGE_SYSTEM_PROMPT)


def test_조율자는_갖지_않은_도구를_부르라고_지시받지_않는다() -> None:
    for tool in ("get_task_events",):
        assert tool not in INVESTIGATOR_SYSTEM_PROMPT
    assert "You do NOT open tasks yourself." in INVESTIGATOR_SYSTEM_PROMPT


def test_조사자는_맡은_후보만_받는다() -> None:
    user = build_inspect_prompt("task-9")

    _show("inspect (조사자)", INSPECT_SYSTEM_PROMPT, user)
    assert user == "Task to judge: task-9"


def test_조율자는_스캔_시점과_상한과_조사_보고를_받는다() -> None:
    reports = [InspectReport(taskId="task-1", archivable=True, reason="빈 작업", citedEventIds=["event-1"])]

    user = build_user_prompt("2026-07-14T00:00:00Z", 3, TASK_CLEANUP_PROMPT.directive("ko"), reports)

    _show("investigate (조율자)", INVESTIGATOR_SYSTEM_PROMPT, user)
    assert "Scan time: 2026-07-14T00:00:00Z" in user
    assert "Propose at most 3 tasks to archive." in user
    assert "What the cleanup candidate reviewers reported:" in user
    assert "- task-1: archivable" in user
    assert "(events: event-1)" in user


def test_수리_지시문은_검증_오류를_그대로_싣는다() -> None:
    directive = REPAIR_DIRECTIVE.format(errors="- task-7은 이번 배치의 후보가 아니다")

    print("\n───────── task-cleanup :: repair (수리 지시문) ─────────")
    print(directive)
    assert "- task-7은 이번 배치의 후보가 아니다" in directive
