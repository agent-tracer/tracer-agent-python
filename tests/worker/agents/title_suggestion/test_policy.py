"""title-suggestion 정책 함수의 후보 정규화 규칙을 검증한다."""

from __future__ import annotations

from tracer_agent.shared.agents.title_suggestion.models import (
    TitleSuggestion,
    TitleSuggestionDraft,
    TitleSuggestionTurn,
)
from tracer_agent.worker.agents.title_suggestion.policy import (
    normalize_title_candidate,
    windowed_turns,
)


def _draft(*titles: str) -> TitleSuggestionDraft:
    return TitleSuggestionDraft(
        suggestions=[TitleSuggestion(title=title, rationale=f"{title} 근거") for title in titles]
    )


def _titles(draft: TitleSuggestionDraft | None) -> list[str]:
    return [] if draft is None else [suggestion.title for suggestion in draft.suggestions]


def test_현재_제목과_겹치는_후보는_사유_없이_지운다() -> None:
    draft = _draft("현재 제목", "첫 제목", "둘째 제목")

    filtered, errors = normalize_title_candidate(draft, "현재 제목")

    assert _titles(filtered) == ["첫 제목", "둘째 제목"]
    assert errors == []


def test_서로_겹치는_후보는_하나만_남기고_지운다() -> None:
    draft = _draft("첫 제목", "둘째 제목", "첫 제목")

    filtered, errors = normalize_title_candidate(draft, "현재 제목")

    assert _titles(filtered) == ["첫 제목", "둘째 제목"]
    assert errors == []


def test_자리표시자_제목은_사유_없이_지운다() -> None:
    draft = _draft("Untitled", "첫 제목", "둘째 제목")

    filtered, errors = normalize_title_candidate(draft, "현재 제목")

    assert _titles(filtered) == ["첫 제목", "둘째 제목"]
    assert errors == []


def test_지우고_나서_둘이_안_되면_그때만_다시_묻는다() -> None:
    draft = _draft("Untitled", "첫 제목", "첫 제목")

    filtered, errors = normalize_title_candidate(draft, "현재 제목")

    assert _titles(filtered) == ["첫 제목"]
    assert len(errors) == 1
    assert "usable suggestion" in errors[0]


def test_모델이_스스로_비운_결과는_다시_묻지_않는다() -> None:
    filtered, errors = normalize_title_candidate(TitleSuggestionDraft(), "현재 제목")

    assert _titles(filtered) == []
    assert errors == []


def test_후보를_아예_받지_못하면_그_사실을_남긴다() -> None:
    filtered, errors = normalize_title_candidate(None, "현재 제목")

    assert filtered is None
    assert errors == ["No title-suggestion candidate was produced."]


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
