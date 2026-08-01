"""title-suggestion의 프롬프트 조합을 콘솔에 펴 보이고 핵심 구성을 단언한다."""

from __future__ import annotations

from tests.support.prompts import TITLE_SUGGESTION_PROMPT
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionContext
from tracer_agent.worker.agents.title_suggestion.prompts import build_prompt_bundle, build_user_prompt

_PROMPTS = build_prompt_bundle(TITLE_SUGGESTION_PROMPT)
INVESTIGATOR_SYSTEM_PROMPT = _PROMPTS["investigatorSystemPrompt"]
REPAIR_DIRECTIVE = _PROMPTS["repairDirective"]

_CONTEXT = {
    "title": "Untitled",
    "status": "completed",
    "workspacePath": "/workspace/project",
    "totalEventCount": 300,
    "totalTurnCount": 25,
    "truncated": True,
    "turns": [
        {
            "turnIndex": 1,
            "askedText": "인증 미들웨어의 토큰 누수를 고쳐줘",
            "assistantText": "회귀 테스트를 추가하고 누수를 수정했습니다.",
        }
    ],
}


def test_조사자는_대화_발췌를_JSON_대신_산문으로_받는다() -> None:
    user = build_user_prompt(
        "task-1", TitleSuggestionContext.model_validate(_CONTEXT), TITLE_SUGGESTION_PROMPT.directive("ko")
    )

    print("\n───────── title-suggestion :: investigate (조사자) ─────────")
    print("[system]")
    print(INVESTIGATOR_SYSTEM_PROMPT)
    print("[user]")
    print(user)
    assert user.splitlines()[:4] == [
        "Task ID: task-1",
        "Current title: Untitled",
        "Status: completed",
        "Workspace: /workspace/project",
    ]
    assert "Activity: 300 events across 25 conversation turns." in user
    assert "(older turns omitted)." in user
    assert "User: 인증 미들웨어의 토큰 누수를 고쳐줘" in user
    assert "Assistant: 회귀 테스트를 추가하고 누수를 수정했습니다." in user
    assert "turnIndex" not in user and "{" not in user


def test_수리_지시문은_검증_오류를_그대로_싣는다() -> None:
    directive = REPAIR_DIRECTIVE.format(errors="- 현재 제목과 같은 제안은 버린다")

    print("\n───────── title-suggestion :: repair (수리 지시문) ─────────")
    print(directive)
    assert "- 현재 제목과 같은 제안은 버린다" in directive
