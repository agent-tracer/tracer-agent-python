"""title-suggestion 구조화 체인이 사용하는 프롬프트."""

from __future__ import annotations

from tracer_agent.shared.agents.shared.models import Language
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext

from ..shared.fragment_registry import ResolvedFragmentSnapshot, resolved_fragment_content
from ..shared.prompt_fragments import render
from .prompt_fragments import LAN_TITLE_SUGGESTION_FRAGMENT_REGISTRY

PROMPT_VERSION = "title-suggestion-native-v5"

LANGUAGE_DIRECTIVES: dict[Language, str] = {
    "auto": (
        "Mirror the user's language: reuse the language of the current title and the user's messages "
        "(Korean to Korean, English to English)."
    ),
    "ko": (
        "Write every title and rationale in Korean (한국어). Translate source text written in another "
        "language rather than echoing it, including names and keywords where a natural translation exists."
    ),
    "en": (
        "Write every title and rationale in English. Translate source text written in another language "
        "rather than echoing it, including names and keywords where a natural translation exists."
    ),
    "ja": (
        "Write every title and rationale in Japanese (日本語). Translate source text written in another "
        "language rather than echoing it, including names and keywords where a natural translation exists."
    ),
    "zh": (
        "Write every title and rationale in Simplified Chinese (简体中文). Translate source text written "
        "in another language rather than echoing it, including names and keywords where a natural "
        "translation exists."
    ),
}


_SYSTEM_TEMPLATE = "lan.title-suggestion.investigator.system"
_REPAIR_TEMPLATE = "lan.title-suggestion.investigator.repair"


def _fragment(key: str, template: str, snapshot: ResolvedFragmentSnapshot | None = None) -> str:
    return render(resolved_fragment_content(LAN_TITLE_SUGGESTION_FRAGMENT_REGISTRY, key, template, snapshot))


# 도구 예산을 무엇으로 세는지는 실행 기계가 소유하므로 근거를 더 캐라는 문단만 이 백엔드가 쓴다.
def build_prompt_bundle(snapshot: ResolvedFragmentSnapshot | None = None) -> dict[str, str]:
    """코드 scaffold에 title 프래그먼트 snapshot을 조립한다."""
    investigator = "\n".join(
        [
            "You propose better titles for one recorded coding-agent task.",
            f"Prompt version: {PROMPT_VERSION}.",
            "",
            _fragment("LAN_CONTEXT_SHAPE", _SYSTEM_TEMPLATE, snapshot),
            "",
            "When the excerpt is enough to name the work, name it without calling any tool. When it is "
            "empty,",
            "ambiguous, truncated, or omits load-bearing work, call get_task_events to read the raw event",
            'sequence: you choose limit and cursor, and order="desc" reads the ending of a long task first.',
            "The tool budget is limited; stop pulling as soon as you can name the work.",
            "",
            _fragment("LAN_TITLE_SPEC", _SYSTEM_TEMPLATE, snapshot),
            "",
            _fragment("LAN_ANSWER_SHAPE", _SYSTEM_TEMPLATE, snapshot),
        ]
    )
    repair = "\n".join(
        [
            "Deterministic validation rejected your output:",
            "{errors}",
            "",
            _fragment("LAN_REPAIR_DIRECTIVE", _REPAIR_TEMPLATE, snapshot),
        ]
    )
    return {"investigatorSystemPrompt": investigator, "repairDirective": repair}


_DEFAULT_PROMPTS = build_prompt_bundle()
LAN_INVESTIGATOR_SYSTEM_PROMPT = _DEFAULT_PROMPTS["investigatorSystemPrompt"]
REPAIR_DIRECTIVE = _DEFAULT_PROMPTS["repairDirective"]

INVESTIGATOR_SYSTEM_PROMPT = LAN_INVESTIGATOR_SYSTEM_PROMPT


# 실행 검증과 부팅 pin이 이 딕셔너리 하나로 번들 키와 내용을 함께 본다.
PROMPT_BUNDLE: dict[str, str] = {"investigatorSystemPrompt": INVESTIGATOR_SYSTEM_PROMPT}


def build_user_prompt(task_id: str, context: TitleSuggestionContext, language: Language) -> str:
    """이름 붙일 대상 태스크와 대화 발췌와 출력 언어를 담은 최초 지시문이다."""
    lines = [
        f"Task ID: {task_id}",
        f"Current title: {context.title}",
        f"Status: {context.status}",
    ]
    if context.workspacePath is not None:
        lines.append(f"Workspace: {context.workspacePath}")
    lines.append(f"Output language: {LANGUAGE_DIRECTIVES[language]}")
    lines.append("")
    lines.append(
        f"Activity: {context.totalEventCount} events across {context.totalTurnCount} conversation turns."
    )
    if context.truncated:
        lines.append(
            f"Showing the first turn and the most recent {len(context.turns) - 1} turns "
            "(older turns omitted)."
        )
    lines.append("")
    if not context.turns:
        lines.append("(no conversation turns recorded)")
    else:
        for turn in context.turns:
            lines.append(f"User: {turn.askedText}")
            if turn.assistantText is not None:
                lines.append(f"Assistant: {turn.assistantText}")
            lines.append("")
    lines.append(
        "If the current title already reads cleanly, return an empty suggestions list. "
        "Otherwise propose 2-3 alternative titles."
    )
    return "\n".join(lines)
