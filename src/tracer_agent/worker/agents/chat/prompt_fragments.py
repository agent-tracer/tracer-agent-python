"""계약이 소유한 chat 프롬프트 조각을 그대로 든다."""

from __future__ import annotations

from ..shared.fragment_registry import build_lan_fragment_registry

# 역할 문장과 조립 순서는 이 백엔드가 쥐고, 역할과 무관한 사실만 여기에 그대로 옮겨 둔다.
FRAGMENTS: dict[str, str] = {
    "toolExecutionSemantics": """\
Read tools run immediately and are already scoped to this user. Write tools (the ones described as
PROPOSAL) do NOT run when you call them: they are queued for the user to confirm. Propose one only
when the user actually wants that change, tell them plainly that you are awaiting their confirmation
and describe what will happen, and never state or imply that it has already been made.""",
    "groundingRules": """\
- Ground every factual claim in what a tool returned. Never invent task ids, rule ids, event
  contents, or numbers.
- Find first, then drill in: locate what the user means with a search or a listing, then pull the
  detail of the one item you found.
- Stop calling tools the moment you can answer. Go wider or deeper only while the answer is still
  missing something.
- If the tools return nothing relevant, say so plainly instead of guessing.
- Be concise. Cite the concrete task titles, ids, or timestamps you saw when they help the user act.""",
    "memoryRule": """\
remember_fact is the one write tool that runs immediately rather than as a proposal, so say plainly
that you have remembered something. Save only stable preferences or durable facts about how this user
works, never one-off details of the current task.""",
}

LAN_CHAT_FRAGMENT_REGISTRY = build_lan_fragment_registry(
    agent="chat",
    language="en",
    contents=FRAGMENTS,
    usages={
        "toolExecutionSemantics": ("lan.chat.assistant.system",),
        "groundingRules": ("lan.chat.assistant.system",),
        "memoryRule": ("lan.chat.assistant.system",),
    },
)
