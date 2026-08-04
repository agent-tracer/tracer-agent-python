"""title-suggestion의 후보 검증과 조건부 분기 정책을 소유한다."""

from __future__ import annotations

import re
import unicodedata

from tracer_agent.shared.agents.title_suggestion.models import (
    RECENT_TURN_LIMIT,
    TitleSuggestion,
    TitleSuggestionDraft,
    TitleSuggestionTurn,
)

from ..runtime.routing import ValidationReasons


def windowed_turns(
    turns: list[TitleSuggestionTurn],
) -> tuple[list[TitleSuggestionTurn], bool]:
    """전체 턴을 최초 턴과 최근 창으로 좁혀 실을 턴과 잘림 여부를 낸다."""
    recent = turns[-RECENT_TURN_LIMIT:]
    included = [turns[0], *recent] if turns and turns[0] not in recent else recent
    return included, len(turns) > len(included)


# 후보 하나가 쓸모없어 떨어질 때의 사유이며 모델이 아니라 코드가 지운다.
MIN_TITLE_SUGGESTIONS = 2
SHORT_OF_MINIMUM = (
    f"only {{kept}} usable suggestion(s) remain after dropping unusable ones; "
    f"return {MIN_TITLE_SUGGESTIONS}-3 distinct titles that differ from the current one"
)


def normalize_title_candidate(
    candidate: TitleSuggestionDraft | None,
    current_title: str,
) -> tuple[TitleSuggestionDraft | None, list[str]]:
    """기계적으로 지울 수 있는 후보를 떨어뜨리고 모델이 고쳐야 하는 부족만 사유로 남긴다."""
    if candidate is None:
        return None, ["No title-suggestion candidate was produced."]
    # 모델이 스스로 비운 결과는 계약이 허용하므로 다시 묻지 않는다.
    if not candidate.suggestions:
        return candidate, []
    kept = _usable(candidate.suggestions, current_title)
    filtered = candidate.model_copy(update={"suggestions": kept})
    if len(kept) < MIN_TITLE_SUGGESTIONS:
        return filtered, [SHORT_OF_MINIMUM.format(kept=len(kept))]
    return filtered, []


def _usable(
    suggestions: list[TitleSuggestion],
    current_title: str,
) -> list[TitleSuggestion]:
    """현재 제목을 되풀이하거나 서로 겹치거나 자리표시자인 후보를 뺀다."""
    current = _normalize_title(current_title)
    seen: set[str] = set()
    kept: list[TitleSuggestion] = []
    for suggestion in suggestions:
        normalized = _normalize_title(suggestion.title)
        if normalized == current or normalized in seen or _is_placeholder(normalized):
            continue
        seen.add(normalized)
        kept.append(suggestion)
    return kept


VALIDATION_REASONS = ValidationReasons(
    passed="candidate passed deterministic title validation",
    repairable="candidate failed validation and one repair attempt remains",
    exhausted="candidate remained invalid after the repair attempt",
)


def _normalize_title(value: str) -> str:
    # 워커의 검증기가 쓰는 자바스크립트 toLowerCase에는 casefold의 합자 확장이 없어 판정이 갈린다.
    return " ".join(unicodedata.normalize("NFKC", value).split()).lower()


def _is_placeholder(normalized: str) -> bool:
    return (
        normalized in {"untitled", "test"} or re.fullmatch(r"task(?:\s|[-_:#])*\d+", normalized) is not None
    )
