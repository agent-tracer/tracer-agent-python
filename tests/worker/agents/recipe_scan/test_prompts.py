"""recipe-scan의 역할별 프롬프트 조합을 콘솔에 펴 보이고 핵심 구성을 단언한다."""

from __future__ import annotations

from tests.support.prompts import RECIPE_SCAN_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import DispatchPlan, Excerpt, ProbeReport
from tracer_agent.worker.agents.recipe_scan.prompts import (
    build_probe_prompt,
    build_prompt_bundle,
    build_survey_prompt,
    build_user_prompt,
)

_PROMPTS = build_prompt_bundle(RECIPE_SCAN_PROMPT)
INVESTIGATOR_SYSTEM_PROMPT = _PROMPTS["investigatorSystemPrompt"]
PROBE_SYSTEM_PROMPT = _PROMPTS["probeSystemPrompt"]
SURVEY_SYSTEM_PROMPT = _PROMPTS["surveySystemPrompt"]
REPAIR_DIRECTIVE = _PROMPTS["repairDirective"]


def _show(role: str, system: str, user: str) -> None:
    print(f"\n───────── recipe-scan :: {role} ─────────")
    print("[system]")
    print(system)
    print("[user]")
    print(user)


def test_계획자는_앵커와_사용자_지시로_계획을_세운다() -> None:
    user = build_survey_prompt("t1", "마이그레이션만 봐줘")

    _show("survey (계획자)", SURVEY_SYSTEM_PROMPT, user)
    assert "Anchor task ID: t1" in user
    assert "What the user asked for: 마이그레이션만 봐줘" in user


def test_전문가는_맡은_질문만_받는다() -> None:
    user = build_probe_prompt("t1", "무엇을 했나")

    _show("probe (전문가)", PROBE_SYSTEM_PROMPT, user)
    assert user == "Anchor task ID: t1\nYour question: 무엇을 했나"
    assert "one specialist" in PROBE_SYSTEM_PROMPT


def test_조율자는_계획과_전문가_보고를_절로_받는다() -> None:
    plan = DispatchPlan.model_validate(
        {"probes": [{"probe": "timeline", "weight": 5, "question": "무엇을 했나"}]}
    )
    reports = [
        ProbeReport(
            probe="timeline",
            verdict="마이그레이션을 확인했다",
            excerpts=[Excerpt(taskId="t1", eventId="event-1", text="마이그레이션")],
        ),
        ProbeReport(probe="rules", verdict="예산 안에서 다 못 봤다", exhausted=True),
    ]

    user = build_user_prompt("t1", None, RECIPE_SCAN_PROMPT.directive("ko"), plan, reports)

    _show("investigate (조율자)", INVESTIGATOR_SYSTEM_PROMPT, user)
    assert "Your own plan for this investigation:" in user
    assert "- timeline (weight 5): 무엇을 했나" in user
    assert "What your specialists reported:" in user
    assert "### timeline" in user
    assert "- [t1/event-1] 마이그레이션" in user
    assert "### rules (budget exhausted)" in user
    assert "coordinator" in INVESTIGATOR_SYSTEM_PROMPT


def test_수리_지시문은_검증_오류를_그대로_싣는다() -> None:
    directive = REPAIR_DIRECTIVE.format(errors="- event-9는 도구가 돌려준 적 없다")

    print("\n───────── recipe-scan :: repair (수리 지시문) ─────────")
    print(directive)
    assert "- event-9는 도구가 돌려준 적 없다" in directive
