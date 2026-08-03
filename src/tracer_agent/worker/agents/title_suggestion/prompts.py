"""title-suggestion 구조화 체인이 사용하는 프롬프트."""

from __future__ import annotations

from dataclasses import dataclass

from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext

from ..shared.prompt_source_port import AgentPrompt


@dataclass(frozen=True)
class TitlePrompts:
    """이 에이전트가 이번 실행에서 쓸 조립된 프롬프트다."""

    investigator_system: str
    repair_directive: str


# 도구 예산을 무엇으로 세는지는 실행 기계가 소유하므로 근거를 더 캐라는 문단만 이 백엔드가 쓴다.
def build_prompt_bundle(prompt: AgentPrompt) -> TitlePrompts:
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
    return TitlePrompts(investigator_system=investigator, repair_directive=repair)


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
