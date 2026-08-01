"""title-suggestion 구조화 체인이 사용하는 프롬프트."""

from __future__ import annotations

from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext

from ..shared.prompt_source_port import AgentPrompt

PROMPT_VERSION = "title-suggestion-native-v5"

# 관측이 조립 결과의 해시를 template 별로 실을 수 있도록 번들 이름과 template key 를 잇는다.
TEMPLATE_KEYS: dict[str, str] = {
    "investigatorSystemPrompt": "title-suggestion.investigator.system",
    "repairDirective": "title-suggestion.investigator.repair",
}


# 도구 예산을 무엇으로 세는지는 실행 기계가 소유하므로 근거를 더 캐라는 문단만 이 백엔드가 쓴다.
def build_prompt_bundle(prompt: AgentPrompt) -> dict[str, str]:
    """받은 조각을 이 에이전트의 scaffold 문장 사이에 끼워 프롬프트 둘을 만든다."""
    template = prompt.template("title-suggestion.investigator.system")
    investigator = "\n".join(
        [
            "You propose better titles for one recorded coding-agent task.",
            "",
            template.slot("contextShape"),
            "",
            "When the excerpt is enough to name the work, name it without calling any tool. When it is "
            "empty,",
            "ambiguous, truncated, or omits load-bearing work, call get_task_events to read the raw event",
            'sequence: you choose limit and cursor, and order="desc" reads the ending of a long task first.',
            "The tool budget is limited; stop pulling as soon as you can name the work.",
            "",
            template.slot("titleSpec"),
            "",
            template.slot("answerShape"),
        ]
    )
    repair = "\n".join(
        [
            "Deterministic validation rejected your output:",
            "{errors}",
            "",
            prompt.template("title-suggestion.investigator.repair").slot("repairDirective"),
        ]
    )
    return {"investigatorSystemPrompt": investigator, "repairDirective": repair}


def build_user_prompt(task_id: str, context: TitleSuggestionContext, directive: str) -> str:
    """이름 붙일 대상 태스크와 대화 발췌와 출력 언어를 담은 최초 지시문이다."""
    lines = [
        f"Task ID: {task_id}",
        f"Current title: {context.title}",
        f"Status: {context.status}",
    ]
    if context.workspacePath is not None:
        lines.append(f"Workspace: {context.workspacePath}")
    lines.append(f"Output language: {directive}")
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
