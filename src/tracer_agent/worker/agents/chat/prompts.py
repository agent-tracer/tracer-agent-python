"""chat 대화 에이전트의 시스템 프롬프트와 컨텍스트 조립을 소유한다."""

from __future__ import annotations

from tracer_agent.shared.agents.chat.models import ChatFact

from ..shared.prompt_source_port import AgentPrompt
from ..shared.safety_policy import SAFETY_POLICY

SYSTEM_TEMPLATE = "chat.assistant.system"


def build_system_prompt(prompt: AgentPrompt) -> str:
    """받은 조각을 이 에이전트의 scaffold 문장 사이에 끼워 시스템 프롬프트를 만든다."""
    template = prompt.template(SYSTEM_TEMPLATE)
    return "\n".join(
        [
            SAFETY_POLICY,
            "",
            "You are the assistant of Agent Tracer, an observability tool that records coding-agent sessions",
            "(tasks), their timelines, verification rules, memos, recipes, tags, cleanup suggestions, "
            "and AI jobs.",
            "",
            "Your job is to work out what the user is actually asking for, reach for the tools that "
            "answer it,",
            "ground your reply in what they return, and propose the changes their work needs.",
            template.slot("toolExecutionSemantics"),
            "",
            "How to work:",
            template.slot("groundingRules"),
            "",
            "Memory:",
            template.slot("memoryRule"),
        ]
    )


def build_context_prompt(directive: str, summary: str | None, facts: list[ChatFact]) -> str:
    """이번 턴의 요약과 사용자 사실과 출력 언어를 담은 선행 컨텍스트 메시지다."""
    lines = [directive]
    if facts:
        lines.append("")
        lines.append('<memory source="untrusted">')
        lines.extend(f"- {fact.key}: {fact.content}" for fact in facts)
        lines.append("</memory>")
    if summary is not None and summary.strip():
        lines.append("")
        lines.append('<summary source="untrusted">')
        lines.append(summary.strip())
        lines.append("</summary>")
    return "\n".join(lines)
