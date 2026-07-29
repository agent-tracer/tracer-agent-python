"""chat 대화 에이전트의 시스템 프롬프트와 컨텍스트 조립을 소유한다."""

from __future__ import annotations

from tracer_agent.shared.agents.chat.models import ChatFact
from tracer_agent.shared.agents.shared.models import Language

from ..shared.fragment_registry import ResolvedFragmentSnapshot, resolved_fragment_content
from ..shared.prompt_fragments import render
from .prompt_fragments import LAN_CHAT_FRAGMENT_REGISTRY

PROMPT_VERSION = "chat-native-v3"

LANGUAGE_DIRECTIVES: dict[Language, str] = {
    "auto": "Reply in the language the user is writing in.",
    "ko": "Reply in Korean (한국어). Translate technical terms naturally.",
    "en": "Reply in English. Translate technical terms naturally.",
    "ja": "Reply in Japanese (日本語). Translate technical terms naturally.",
    "zh": "Reply in Simplified Chinese (简体中文). Translate technical terms naturally.",
}


_TEMPLATE = "lan.chat.assistant.system"


def _fragment(key: str, snapshot: ResolvedFragmentSnapshot | None = None) -> str:
    return render(resolved_fragment_content(LAN_CHAT_FRAGMENT_REGISTRY, key, _TEMPLATE, snapshot))


def build_system_prompt(snapshot: ResolvedFragmentSnapshot | None = None) -> str:
    """코드 scaffold에 시작 시 고정된 chat 프래그먼트 snapshot을 조립한다."""
    return "\n".join(
        [
            "You are the assistant of Agent Tracer, an observability tool that records coding-agent sessions",
            "(tasks), their timelines, verification rules, memos, recipes, tags, cleanup suggestions, "
            "AI jobs",
            "and settings.",
            f"Prompt version: {PROMPT_VERSION}.",
            "",
            "Your job is to work out what the user is actually asking for, reach for the tools that "
            "answer it,",
            "ground your reply in what they return, and propose the changes their work needs.",
            _fragment("LAN_TOOL_EXECUTION_SEMANTICS", snapshot),
            "",
            "How to work:",
            _fragment("LAN_GROUNDING_RULES", snapshot),
            "",
            "Memory:",
            _fragment("LAN_MEMORY_RULE", snapshot),
        ]
    )


LAN_ASSISTANT_SYSTEM_PROMPT = build_system_prompt()

SYSTEM_PROMPT = LAN_ASSISTANT_SYSTEM_PROMPT


def build_context_prompt(summary: str | None, facts: list[ChatFact], language: Language) -> str:
    """이번 턴의 요약과 사용자 사실과 출력 언어를 담은 선행 컨텍스트 메시지다."""
    lines = [LANGUAGE_DIRECTIVES[language]]
    if facts:
        lines.append("")
        lines.append("Durable facts you remember about this user:")
        lines.extend(f"- {fact.key}: {fact.content}" for fact in facts)
    if summary is not None and summary.strip():
        lines.append("")
        lines.append("Summary of earlier conversation in this thread:")
        lines.append(summary.strip())
    return "\n".join(lines)
