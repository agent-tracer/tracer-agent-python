"""recipe-scan 도구 루프가 사용하는 프롬프트."""

from __future__ import annotations

from collections.abc import Sequence

from tracer_agent.shared.agents.recipe_scan.models import MAX_RECIPE_CANDIDATES, DispatchPlan, ProbeReport
from tracer_agent.shared.agents.shared.models import Language

from ..shared.fragment_registry import ResolvedFragmentSnapshot, resolved_fragment_content
from ..shared.prompt_fragments import render
from .prompt_fragments import LAN_RECIPE_SCAN_FRAGMENT_REGISTRY

# 프롬프트 버전은 실행 궤적과 평가 코퍼스에서 의미 변화의 경계를 식별하는 값이다.
PROMPT_VERSION = "recipe-scan-native-v8"

LANGUAGE_DIRECTIVES: dict[Language, str] = {
    "auto": "Reuse the dominant language of the anchor task (Korean to Korean, English to English).",
    "ko": (
        "Write prose fields in Korean (한국어): title, intent, description, summary_md, request, "
        "corrections, pitfalls, steps[].rationale, and rationale. Translate source text written in "
        "another language rather than echoing it. Keep identifiers, file paths, tool names, commands, "
        "taskIds, and eventIds verbatim."
    ),
    "en": (
        "Write prose fields in English: title, intent, description, summary_md, request, corrections, "
        "pitfalls, steps[].rationale, and rationale. Translate source text written in another language "
        "rather than echoing it. Keep identifiers, file paths, tool names, commands, taskIds, and "
        "eventIds verbatim."
    ),
    "ja": (
        "Write prose fields in Japanese (日本語): title, intent, description, summary_md, request, "
        "corrections, pitfalls, steps[].rationale, and rationale. Translate source text written in "
        "another language rather than echoing it. Keep identifiers, file paths, tool names, commands, "
        "taskIds, and eventIds verbatim."
    ),
    "zh": (
        "Write prose fields in Simplified Chinese (简体中文): title, intent, description, summary_md, "
        "request, corrections, pitfalls, steps[].rationale, and rationale. Translate source text "
        "written in another language rather than echoing it. Keep identifiers, file paths, tool names, "
        "commands, taskIds, and eventIds verbatim."
    ),
}


def _fragment(key: str, template: str, snapshot: ResolvedFragmentSnapshot | None = None) -> str:
    return render(resolved_fragment_content(LAN_RECIPE_SCAN_FRAGMENT_REGISTRY, key, template, snapshot))


LAN_INVESTIGATOR_SYSTEM_PROMPT = "\n".join(
    [
        "You are the coordinator of a recipe-scan investigation. You mine the specialists' reports for",
        'reusable "recipes".',
        f"Prompt version: {PROMPT_VERSION}.",
        "",
        _fragment("LAN_RECIPE_DEFINITION", "lan.recipe-scan.investigator.system"),
        "",
        _fragment("LAN_EVIDENCE_SOURCING", "lan.recipe-scan.investigator.system"),
        "",
        "How to work:",
        _fragment("LAN_TURN_SPLITTING", "lan.recipe-scan.investigator.system"),
        "",
        _fragment("LAN_CITATION_DISCIPLINE", "lan.recipe-scan.investigator.system"),
        "",
        _fragment("LAN_CANDIDATE_BUDGET", "lan.recipe-scan.investigator.system"),
        "",
        _fragment("LAN_REDISPATCH_PROTOCOL", "lan.recipe-scan.investigator.system"),
        "",
        _fragment("LAN_OUTPUT_FIELDS", "lan.recipe-scan.investigator.system"),
        "",
        "Rules:",
        _fragment("LAN_QUALITY_RULES", "lan.recipe-scan.investigator.system"),
        "",
        "When the evidence is enough, stop calling tools and emit the structured output.",
    ]
)

REPAIR_DIRECTIVE = "\n".join(
    [
        "Deterministic provenance validation rejected your output:",
        "{errors}",
        "",
        _fragment("LAN_REPAIR_DIRECTIVE", "lan.recipe-scan.investigator.repair"),
    ]
)


def build_user_prompt(
    task_id: str,
    user_prompt: str | None,
    language: Language,
    plan: DispatchPlan | None = None,
    reports: Sequence[ProbeReport] | None = None,
) -> str:
    """앵커 태스크와 사용자 지시와 출력 언어와 스스로 세운 계획을 담은 최초 지시문이다."""
    lines = [f"Anchor taskId: {task_id}"]
    if user_prompt:
        lines.append(f"User direction: {user_prompt}")
    lines.append(f"Output language: {LANGUAGE_DIRECTIVES[language]}")
    lines.append(f"Mine this task for up to {MAX_RECIPE_CANDIDATES} recipe candidates.")
    return "\n".join(lines) + render_plan(plan) + render_reports(reports)


LAN_SURVEY_SYSTEM_PROMPT = "\n".join(
    [
        "You plan one recipe-scan investigation before it starts.",
        f"Prompt version: {PROMPT_VERSION}.",
        "",
        _fragment("LAN_SPECIALIST_CATALOG", "lan.recipe-scan.survey.system"),
        "",
        _fragment("LAN_DISPATCH_WEIGHTING", "lan.recipe-scan.survey.system"),
        "",
        _fragment("LAN_EMPTY_PLAN", "lan.recipe-scan.survey.system"),
    ]
)


def render_reports(reports: Sequence[ProbeReport] | None) -> str:
    """전문가들이 올린 보고를 조율자가 읽을 근거로 편다."""
    if not reports:
        return ""
    blocks = []
    for report in reports:
        lines = [f"### {report.probe}" + (" (budget exhausted)" if report.exhausted else "")]
        lines.append(report.verdict)
        lines.extend(f"- [{excerpt.taskId}/{excerpt.eventId}] {excerpt.text}" for excerpt in report.excerpts)
        blocks.append("\n".join(lines))
    return "\n\nWhat your specialists reported:\n\n" + "\n\n".join(blocks)


def render_plan(plan: DispatchPlan | None) -> str:
    """조율자가 세운 계획을 조사자가 읽을 지시문으로 편다."""
    if plan is None or not plan.probes:
        return ""
    lines = [f"- {probe.probe} (weight {probe.weight}): {probe.question}" for probe in plan.probes]
    return "\n\nYour own plan for this investigation:\n" + "\n".join(lines)


def build_survey_prompt(task_id: str, user_prompt: str | None) -> str:
    """조율자가 조사 계획을 세우는 데 필요한 사실만 싣는다."""
    lines = [f"Anchor task ID: {task_id}"]
    if user_prompt:
        lines.append(f"What the user asked for: {user_prompt}")
    return "\n".join(lines)


# 예산 소진을 무엇으로 세는지는 실행 기계가 소유하므로 조각이 아니라 이 백엔드가 마지막 문장을 쓴다.
LAN_PROBE_SYSTEM_PROMPT = "\n".join(
    [
        "You are one specialist in a recipe-scan investigation.",
        f"Prompt version: {PROMPT_VERSION}.",
        "",
        _fragment("LAN_SPECIALIST_CHARTER", "lan.recipe-scan.probe.system"),
        "",
        _fragment("LAN_SPECIALIST_REPORTING", "lan.recipe-scan.probe.system"),
        "If your budget runs out with the question still open, say so in exhausted so the coordinator",
        "can decide whether to spend more.",
    ]
)

INVESTIGATOR_SYSTEM_PROMPT = LAN_INVESTIGATOR_SYSTEM_PROMPT
SURVEY_SYSTEM_PROMPT = LAN_SURVEY_SYSTEM_PROMPT
PROBE_SYSTEM_PROMPT = LAN_PROBE_SYSTEM_PROMPT


def build_probe_prompt(task_id: str, question: str) -> str:
    """전문가가 자기 질문 하나에 집중하도록 맡은 범위만 싣는다."""
    return "\n".join([f"Anchor task ID: {task_id}", f"Your question: {question}"])


# 실행 검증과 부팅 pin이 이 딕셔너리 하나로 번들 키와 내용을 함께 본다.
PROMPT_BUNDLE: dict[str, str] = {
    "investigatorSystemPrompt": INVESTIGATOR_SYSTEM_PROMPT,
    "probeSystemPrompt": PROBE_SYSTEM_PROMPT,
    "surveySystemPrompt": SURVEY_SYSTEM_PROMPT,
}


def build_resolved_prompt_bundle(snapshot: ResolvedFragmentSnapshot | None = None) -> dict[str, str]:
    """기본 prompt 여덟 문자열 중 recipe 세 개를 snapshot의 slot 본문으로 치환한다."""
    if snapshot is None:
        return {**PROMPT_BUNDLE, "repairDirective": REPAIR_DIRECTIVE}
    templates = {
        "investigatorSystemPrompt": "lan.recipe-scan.investigator.system",
        "probeSystemPrompt": "lan.recipe-scan.probe.system",
        "surveySystemPrompt": "lan.recipe-scan.survey.system",
        "repairDirective": "lan.recipe-scan.investigator.repair",
    }
    source = {**PROMPT_BUNDLE, "repairDirective": REPAIR_DIRECTIVE}
    return {
        name: _replace_fragments(prompt, template, snapshot)
        for name, (prompt, template) in {
            name: (source[name], template) for name, template in templates.items()
        }.items()
    }


def _replace_fragments(prompt: str, template: str, snapshot: ResolvedFragmentSnapshot) -> str:
    resolved = prompt
    for code_name, fragment in LAN_RECIPE_SCAN_FRAGMENT_REGISTRY.items():
        if template not in fragment.template_keys:
            continue
        local = render(fragment.content)
        replacement = _fragment(code_name, template, snapshot)
        if local not in resolved:
            raise ValueError(f"fragment scaffold slot is missing: {template}/{code_name}")
        resolved = resolved.replace(local, replacement, 1)
    return resolved
