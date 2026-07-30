"""task-cleanup 도구 루프가 사용하는 프롬프트."""

from __future__ import annotations

from collections.abc import Sequence

from tracer_agent.shared.agents.shared.models import Language
from tracer_agent.shared.agents.task_cleanup.models import InspectReport

from ..shared.fragment_registry import ResolvedFragmentSnapshot, resolved_fragment_content
from ..shared.prompt_fragments import render
from .prompt_fragments import TASK_CLEANUP_FRAGMENT_REGISTRY

PROMPT_VERSION = "task-cleanup-native-v6"

LANGUAGE_DIRECTIVES: dict[Language, str] = {
    "auto": (
        "Reuse the dominant language of the candidate task titles (Korean to Korean, English to English)."
    ),
    "ko": (
        "Write every rationale in Korean (한국어). Translate source text written in another language "
        "rather than echoing it, and render technical terms naturally."
    ),
    "en": (
        "Write every rationale in English. Translate source text written in another language rather "
        "than echoing it, and render technical terms naturally."
    ),
    "ja": (
        "Write every rationale in Japanese (日本語). Translate source text written in another language "
        "rather than echoing it, and render technical terms naturally."
    ),
    "zh": (
        "Write every rationale in Simplified Chinese (简体中文). Translate source text written in "
        "another language rather than echoing it, and render technical terms naturally."
    ),
}


def _fragment(key: str, template: str, snapshot: ResolvedFragmentSnapshot | None = None) -> str:
    return render(resolved_fragment_content(TASK_CLEANUP_FRAGMENT_REGISTRY, key, template, snapshot))


INVESTIGATOR_SYSTEM_PROMPT = "\n".join(
    [
        "You are the coordinator of a task-cleanup scan for Agent Tracer, an observability tool that",
        "records coding-agent sessions.",
        f"Prompt version: {PROMPT_VERSION}.",
        "",
        "Your job is to decide which cleanup candidates should be archived, and to write one short",
        "rationale for each.",
        _fragment("TASK_CLEANUP_REVIEW_GUARANTEE", "task-cleanup.investigator.system"),
        "",
        _fragment("TASK_CLEANUP_REVIEWER_SOURCING", "task-cleanup.investigator.system"),
        "",
        "Evidence discipline. This is the rule that matters:",
        _fragment("TASK_CLEANUP_EVIDENCE_DISCIPLINE", "task-cleanup.investigator.system"),
        "",
        "Rules:",
        _fragment("TASK_CLEANUP_SUGGESTION_RULES", "task-cleanup.investigator.system"),
        "",
        _fragment("TASK_CLEANUP_REDISPATCH_PROTOCOL", "task-cleanup.investigator.system"),
        "",
        "Return the suggestions as structured output conforming to the provided schema.",
    ]
)

REPAIR_DIRECTIVE = "\n".join(
    [
        "Deterministic validation rejected part of your output:",
        "{errors}",
        "",
        _fragment("TASK_CLEANUP_REPAIR_DIRECTIVE", "task-cleanup.investigator.repair"),
    ]
)


def build_user_prompt(
    scanned_at: str,
    max_suggestions: int,
    language: Language,
    reports: Sequence[InspectReport] | None = None,
) -> str:
    """정리 스캔 시점과 제안 상한과 출력 언어와 조사 결과를 담은 최초 지시문이다."""
    return "\n".join(
        [
            f"Scan time: {scanned_at}",
            f"Propose at most {max_suggestions} tasks to archive.",
            f"Output language: {LANGUAGE_DIRECTIVES[language]}",
        ]
    ) + render_reports(reports)


TRIAGE_SYSTEM_PROMPT = "\n".join(
    [
        "You open the cleanup scan by choosing which candidates to hand to reviewers.",
        f"Prompt version: {PROMPT_VERSION}.",
        "",
        _fragment("TASK_CLEANUP_CANDIDATE_FIELDS", "task-cleanup.triage.system"),
        "",
        _fragment("TASK_CLEANUP_TRIAGE_POLICY", "task-cleanup.triage.system"),
        "",
        _fragment("TASK_CLEANUP_INSPECT_WEIGHTING", "task-cleanup.triage.system"),
    ]
)

INSPECT_SYSTEM_PROMPT = "\n".join(
    [
        "You judge one cleanup candidate by reading what actually happened in it.",
        f"Prompt version: {PROMPT_VERSION}.",
        "",
        _fragment("TASK_CLEANUP_REVIEWER_CHARTER", "task-cleanup.inspect.system"),
    ]
)


def build_triage_prompt(candidate_count: int) -> str:
    """조율자가 무엇을 열어볼지 정하는 데 필요한 사실만 싣는다."""
    return "\n".join(
        [
            f"Candidates in this batch: {candidate_count}",
            "Call list_candidate_tasks to see them before deciding.",
        ]
    )


def build_inspect_prompt(task_id: str) -> str:
    """조사자가 후보 하나에만 집중하도록 맡은 범위만 싣는다."""
    return f"Task to judge: {task_id}"


def render_reports(reports: Sequence[InspectReport] | None) -> str:
    """후보별 조사 결과를 조율자가 읽을 근거로 편다."""
    if not reports:
        return ""
    lines = [
        f"- {report.taskId}: {'archivable' if report.archivable else 'keep'} — {report.reason}"
        + (f" (events: {', '.join(report.citedEventIds)})" if report.citedEventIds else "")
        for report in reports
    ]
    return "\n\nWhat the cleanup candidate reviewers reported:\n" + "\n".join(lines)


# 실행 검증과 부팅 pin이 이 딕셔너리 하나로 번들 키와 내용을 함께 본다.
PROMPT_BUNDLE: dict[str, str] = {
    "inspectSystemPrompt": INSPECT_SYSTEM_PROMPT,
    "investigatorSystemPrompt": INVESTIGATOR_SYSTEM_PROMPT,
    "triageSystemPrompt": TRIAGE_SYSTEM_PROMPT,
}


def build_resolved_prompt_bundle(snapshot: ResolvedFragmentSnapshot | None = None) -> dict[str, str]:
    """기본 prompt 여덟 문자열 중 cleanup 세 개를 snapshot의 slot 본문으로 치환한다."""
    if snapshot is None:
        return {**PROMPT_BUNDLE, "repairDirective": REPAIR_DIRECTIVE}
    templates = {
        "inspectSystemPrompt": "task-cleanup.inspect.system",
        "investigatorSystemPrompt": "task-cleanup.investigator.system",
        "triageSystemPrompt": "task-cleanup.triage.system",
        "repairDirective": "task-cleanup.investigator.repair",
    }
    source = {**PROMPT_BUNDLE, "repairDirective": REPAIR_DIRECTIVE}
    return {
        name: _replace_fragments(source[name], template, snapshot) for name, template in templates.items()
    }


def _replace_fragments(prompt: str, template: str, snapshot: ResolvedFragmentSnapshot) -> str:
    resolved = prompt
    for code_name, fragment in TASK_CLEANUP_FRAGMENT_REGISTRY.items():
        if template not in fragment.template_keys:
            continue
        local = render(fragment.content)
        replacement = _fragment(code_name, template, snapshot)
        if local not in resolved:
            raise ValueError(f"fragment scaffold slot is missing: {template}/{code_name}")
        resolved = resolved.replace(local, replacement, 1)
    return resolved
