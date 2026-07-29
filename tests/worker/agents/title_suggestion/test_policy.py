"""title-suggestion 정책 함수의 후보 검증 규칙을 검증한다."""

from __future__ import annotations

from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestion,
    TitleSuggestionDraft,
    TitleSuggestionTurn,
)
from tracer_agent.worker.agents.title_suggestion.policy import validate_title_candidate, windowed_turns


def test_후보는_둘_이상이어야_하고_중복과_현재_제목을_되풀이하면_거부한다() -> None:
    single = TitleSuggestionDraft(suggestions=[TitleSuggestion(title="유일한 제목", rationale="근거")])
    assert "suggestions must be empty or contain 2-3 items" in validate_title_candidate(single, "현재 제목")

    repeated = TitleSuggestionDraft(
        suggestions=[
            TitleSuggestion(title="현재 제목", rationale="현재를 되풀이한다"),
            TitleSuggestion(title="다른 제목", rationale="근거"),
            TitleSuggestion(title="다른 제목", rationale="같은 제목을 되풀이한다"),
        ]
    )

    errors = validate_title_candidate(repeated, "현재 제목")

    assert "suggestion 1 repeats the current title" in errors
    assert "suggestion 3 duplicates another suggestion" in errors


def test_자리표시자_제목_후보를_거부한다() -> None:
    draft = TitleSuggestionDraft(
        suggestions=[
            TitleSuggestion(title="Untitled", rationale="자리표시자를 그대로 낸다"),
            TitleSuggestion(title="Task 12", rationale="자동 생성된 자리표시자다"),
        ]
    )

    errors = validate_title_candidate(draft, "현재 제목")

    assert "suggestion 1 is a placeholder title" in errors
    assert "suggestion 2 is a placeholder title" in errors


def _turn(index: int) -> TitleSuggestionTurn:
    return TitleSuggestionTurn(turnIndex=index, askedText=f"질문 {index}", assistantText=f"답변 {index}")


def test_턴이_창보다_적으면_그대로_담고_잘리지_않는다() -> None:
    turns = [_turn(index) for index in range(5)]

    included, truncated = windowed_turns(turns)

    assert included == turns
    assert truncated is False


def test_턴이_창보다_많으면_최초_턴과_최근_20개만_담고_잘림을_알린다() -> None:
    turns = [_turn(index) for index in range(25)]

    included, truncated = windowed_turns(turns)

    assert truncated is True
    assert included[0] == turns[0]
    assert included[1:] == turns[-20:]
    assert len(included) == 21


def test_턴이_없으면_빈_창을_낸다() -> None:
    included, truncated = windowed_turns([])

    assert included == []
    assert truncated is False
