"""계약이 소유한 title-suggestion 프롬프트 조각을 그대로 든다."""

from __future__ import annotations

from ..shared.fragment_registry import build_fragment_registry

# 역할 문장과 조립 순서는 이 백엔드가 쥐고, 역할과 무관한 사실만 여기에 그대로 옮겨 둔다.
FRAGMENTS: dict[str, str] = {
    "contextShape": """\
The user message carries the task's current title and an excerpt of its conversation turns (what the
user asked, what the agent reported back): the oldest user turn plus the most recent turns. Turns in
the middle are dropped when the task is long, and the excerpt says so when that happened.""",
    "titleSpec": """\
Each title must be concrete — naming the area or action — under 80 characters, and normally 4-9 words
in languages where words are space-delimited. Prefer an imperative or noun phrase, as in "Fix auth
middleware token leak" or "Migrate billing schema to v2". Never use a placeholder such as "Task 123",
"Untitled", or "Test".""",
    "answerShape": """\
If the current title is already concrete, accurate, and readable, return an empty list: that is a
real answer, not a failure. Otherwise return exactly 2-3 distinct alternatives. Do not repeat the
current title or another suggestion. Each rationale is one evidence-grounded sentence under 200
characters explaining what drove the suggestion. Do not invent work the evidence does not show.""",
    "repairDirective": """\
Change only what is necessary to satisfy these errors. Return either an empty suggestions list or 2-3
distinct alternatives. Do not repeat the current title, use placeholder titles, or invent work the
evidence does not show. Then return the complete repaired suggestion list.""",
}

TITLE_SUGGESTION_FRAGMENT_REGISTRY = build_fragment_registry(
    agent="title-suggestion",
    language="en",
    contents=FRAGMENTS,
    usages={
        "contextShape": ("title-suggestion.investigator.system",),
        "titleSpec": ("title-suggestion.investigator.system",),
        "answerShape": ("title-suggestion.investigator.system",),
        "repairDirective": ("title-suggestion.investigator.repair",),
    },
)
