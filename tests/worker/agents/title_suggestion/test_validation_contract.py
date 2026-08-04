"""title-suggestion 후보 정규화를 계약의 케이스로 검증한다."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.contract import agent_cases
from tracer_agent.shared.agents.title_suggestion.models import TitleSuggestionDraft
from tracer_agent.worker.agents.title_suggestion.policy import normalize_title_candidate

_CONTRACT = agent_cases("title-suggestion")["cases"]


@pytest.mark.parametrize("case", _CONTRACT["cases"], ids=lambda case: str(case["name"]))
def test_계약의_케이스마다_같은_잔여_목록과_같은_사유를_낸다(case: dict[str, Any]) -> None:
    candidate = TitleSuggestionDraft.model_validate({"suggestions": case["suggestions"]})

    filtered, errors = normalize_title_candidate(candidate, _CONTRACT["currentTitle"])

    kept = [] if filtered is None else [suggestion.title for suggestion in filtered.suggestions]
    assert kept == case["expect"]["kept"]
    assert errors == case["expect"]["errors"]
