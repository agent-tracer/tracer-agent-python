"""title-suggestion의 후보 검증과 조건부 분기 정책을 소유한다."""

from __future__ import annotations

import re
import unicodedata

from tracer_agent.shared.agents.title_suggestion.models import (
    RECENT_TURN_LIMIT,
    TitleSuggestionDraft,
    TitleSuggestionState,
    TitleSuggestionTurn,
)

from ..runtime.execution.trace import ExecutionTrace
from ..runtime.routes import ValidationRoute
from ..runtime.routing import build_validation_router


def windowed_turns(
    turns: list[TitleSuggestionTurn],
) -> tuple[list[TitleSuggestionTurn], bool]:
    """전체 턴을 최초 턴과 최근 창으로 좁혀 실을 턴과 잘림 여부를 낸다."""
    recent = turns[-RECENT_TURN_LIMIT:]
    included = [turns[0], *recent] if turns and turns[0] not in recent else recent
    return included, len(turns) > len(included)


def validate_title_candidate(
    candidate: TitleSuggestionDraft | None,
    current_title: str,
) -> list[str]:
    """제목 후보의 수·중복·자리표시자 제약을 검증한다."""
    if candidate is None:
        return ["No title-suggestion candidate was produced."]
    suggestions = candidate.suggestions
    if not suggestions:
        return []
    errors: list[str] = []
    if len(suggestions) < 2:
        errors.append("suggestions must be empty or contain 2-3 items")
    current = _normalize_title(current_title)
    seen: set[str] = set()
    for index, suggestion in enumerate(suggestions):
        normalized = _normalize_title(suggestion.title)
        if normalized == current:
            errors.append(f"suggestion {index + 1} repeats the current title")
        if normalized in seen:
            errors.append(f"suggestion {index + 1} duplicates another suggestion")
        seen.add(normalized)
        if _is_placeholder(normalized):
            errors.append(f"suggestion {index + 1} is a placeholder title")
    return errors


def build_routes(trace: ExecutionTrace, validation_node: str) -> ValidationRoute[TitleSuggestionState]:
    """후보 검증 결과에 따른 분기 함수를 만든다."""
    return build_validation_router(
        trace,
        validation_node,
        pass_reason="candidate passed deterministic title validation",
        repair_reason="candidate failed validation and one repair attempt remains",
        exhausted_reason="candidate remained invalid after the repair attempt",
    )


def _normalize_title(value: str) -> str:
    # 워커의 검증기가 쓰는 자바스크립트 toLowerCase에는 casefold의 합자 확장이 없어 판정이 갈린다.
    return " ".join(unicodedata.normalize("NFKC", value).split()).lower()


def _is_placeholder(normalized: str) -> bool:
    return (
        normalized in {"untitled", "test"} or re.fullmatch(r"task(?:\s|[-_:#])*\d+", normalized) is not None
    )
