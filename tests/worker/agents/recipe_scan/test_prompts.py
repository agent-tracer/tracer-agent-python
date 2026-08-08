"""recipe-scan의 역할별 프롬프트 조합을 콘솔에 출력하고 핵심 구성을 단언한다."""

from __future__ import annotations

import re
from typing import Any

from tests.support.contract import agent_tools, conformance_case
from tests.support.prompts import RECIPE_SCAN_PROMPT
from tracer_agent.shared.agents.recipe_scan.models import (
    DispatchPlan,
    Excerpt,
    ProbeAssignment,
    ProbeReport,
)
from tracer_agent.worker.agents.recipe_scan.prompts import (
    build_probe_prompt,
    build_prompt_bundle,
    build_survey_prompt,
    build_user_prompt,
)

_PROMPTS = build_prompt_bundle(RECIPE_SCAN_PROMPT)
INVESTIGATOR_SYSTEM_PROMPT = _PROMPTS.investigator_system
PROBE_SYSTEM_PROMPT = _PROMPTS.probe_system
SURVEY_SYSTEM_PROMPT = _PROMPTS.survey_system
REPAIR_DIRECTIVE = _PROMPTS.repair_directive


def _show(role: str, system: str, user: str) -> None:
    print(f"\n───────── recipe-scan :: {role} ─────────")
    print("[system]")
    print(system)
    print("[user]")
    print(user)


def test_계획자는_앵커와_사용자_지시와_쓸_수_있는_턴을_받는다() -> None:
    user = build_survey_prompt(RECIPE_SCAN_PROMPT, "t1", "마이그레이션만 봐줘", 9)

    _show("survey (계획자)", SURVEY_SYSTEM_PROMPT, user)
    assert "Anchor task ID: t1" in user
    assert "What the user asked for: 마이그레이션만 봐줘" in user
    assert "Turns available: 9" in user


def test_전문가는_맡은_질문과_쓸_수_있는_턴을_받는다() -> None:
    user = build_probe_prompt(RECIPE_SCAN_PROMPT, "t1", "무엇을 했나", 4)

    _show("probe (전문가)", PROBE_SYSTEM_PROMPT, user)
    assert "Anchor task ID: t1" in user
    assert "Your question: 무엇을 했나" in user
    assert "Turns available: 4" in user
    assert "one specialist" in PROBE_SYSTEM_PROMPT


def test_전문가는_다른_전문가가_맡은_축을_함께_받는다() -> None:
    siblings = [ProbeAssignment(probe="rules", depth="deep", question="어떤 규칙이 걸렸나")]

    user = build_probe_prompt(RECIPE_SCAN_PROMPT, "t1", "무엇을 했나", 4, siblings)

    assert "rules" in user
    assert "어떤 규칙이 걸렸나" in user


def test_혼자_도는_전문가에게는_남의_담당을_싣지_않는다() -> None:
    user = build_probe_prompt(RECIPE_SCAN_PROMPT, "t1", "무엇을 했나", 4)

    assert "rules" not in user
    assert "repetition" not in user


def test_조율자는_계획과_전문가_보고를_절로_받는다() -> None:
    plan = DispatchPlan.model_validate(
        {"probes": [{"probe": "timeline", "depth": "deep", "question": "무엇을 했나"}]}
    )
    reports = [
        ProbeReport(
            probe="timeline",
            verdict="마이그레이션을 확인했다",
            excerpts=[Excerpt(taskId="t1", eventId="event-1", text="마이그레이션")],
        ),
        ProbeReport(probe="rules", verdict="예산 안에서 다 못 봤다", exhausted=True),
    ]

    user = build_user_prompt(
        RECIPE_SCAN_PROMPT, "t1", None, RECIPE_SCAN_PROMPT.directive("ko"), plan, reports
    )

    _show("investigate (조율자)", INVESTIGATOR_SYSTEM_PROMPT, user)
    assert "Your own plan for this investigation:" in user
    assert "- timeline (deep): 무엇을 했나" in user
    assert "What your specialists reported:" in user
    assert "### timeline" in user
    assert "- [t1/event-1] 마이그레이션" in user
    assert "### rules (budget exhausted)" in user
    assert "coordinator" in INVESTIGATOR_SYSTEM_PROMPT


def test_수리_지시문은_직전_산출과_검증_오류를_함께_싣는다() -> None:
    directive = REPAIR_DIRECTIVE.format(previous='{"recipes":[]}', errors="- event-9는 도구가 돌려준 적 없다")

    print("\n───────── recipe-scan :: repair (수리 지시문) ─────────")
    print(directive)
    assert "- event-9는 도구가 돌려준 적 없다" in directive
    assert '{"recipes":[]}' in directive


_PROMPT_PARITY = conformance_case("recipe.prompt")


_DECLARED_LIMITS = agent_tools("recipe-scan")["limits"]


def _assert_parity(rendered: str, declared: dict[str, Any]) -> None:
    for needle in declared.get("mustContain", []):
        assert needle in rendered, f"{declared['name']}: {needle}"
    for needle in declared.get("mustNotContain", []):
        assert needle not in rendered, f"{declared['name']}: {needle}"
    # 케이스가 숫자를 복제하지 않도록 상한은 이름으로 적고 값은 계약에서 찾는다.
    for key in declared.get("mustContainLimits", []):
        value = _DECLARED_LIMITS[key]
        # 12 가 1200 안에서 걸리면 싣지 않은 상한도 통과하므로 수 전체로 비교한다.
        assert re.search(rf"(?<!\d){value}(?!\d)", rendered), f"{declared['name']}: {key}"


def test_계약이_적은_계획_프롬프트_케이스를_모두_만족한다() -> None:
    for declared in _PROMPT_PARITY["survey"]["cases"]:
        given = declared["input"]
        rendered = build_survey_prompt(
            RECIPE_SCAN_PROMPT, given["taskId"], given.get("userPrompt"), given["availableTurns"]
        )

        _assert_parity(rendered, declared)


def test_계약이_적은_전문가_프롬프트_케이스를_모두_만족한다() -> None:
    for declared in _PROMPT_PARITY["probe"]["cases"]:
        given = declared["input"]
        siblings = [ProbeAssignment.model_validate(one) for one in given.get("siblings", [])]
        rendered = build_probe_prompt(
            RECIPE_SCAN_PROMPT, given["taskId"], given["question"], given["turns"], siblings
        )

        _assert_parity(rendered, declared)


def test_전문가_시스템_프롬프트가_보고를_자르는_상한을_싣는다() -> None:
    for declared in _PROMPT_PARITY["probeSystem"]["cases"]:
        _assert_parity(PROBE_SYSTEM_PROMPT, declared)


def test_조율자_시스템_프롬프트가_산출을_자르는_상한을_싣는다() -> None:
    for declared in _PROMPT_PARITY["investigateSystem"]["cases"]:
        _assert_parity(INVESTIGATOR_SYSTEM_PROMPT, declared)
